"""Agent loop：动作协议、终止条件、重复检测和 observation 截断。"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .context import ContextConfig, CompactionInfo, ContextManager
from ..llm import LLMClient, LLMProtocolError
from ..sandbox.base import Sandbox
from ..tools.base import ToolRegistry
from ..types import LLMResponse, Message, RunResult, Usage
from ..utils import truncate_middle
from .parser import Action, actions_from_tool_calls, parse_bash_action
from .prompt import (
    BASH_SYSTEM_PROMPT,
    TOOLS_SYSTEM_PROMPT,
    build_task_prompt,
    prompt_version,
)
from .trajectory import TrajectoryWriter


@dataclass(frozen=True)
class LoopConfig:
    max_steps: int = 10
    command_timeout: int = 60
    temperature: float = 0.0
    mode: Literal["bash", "tools"] = "tools"
    max_observation_chars: int = 12_000
    max_prompt_tokens: int | None = None
    keep_recent_messages: int = 8
    repeat_warn_threshold: int = 3
    repeat_stop_threshold: int = 5
    submit_check_command: str | None = None
    max_repair_attempts: int = 2

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        if self.command_timeout < 1:
            raise ValueError("command_timeout must be >= 1")
        if self.mode not in ("bash", "tools"):
            raise ValueError("mode must be 'bash' or 'tools'")
        if self.max_observation_chars < 1:
            raise ValueError("max_observation_chars must be >= 1")
        if self.max_prompt_tokens is not None and self.max_prompt_tokens < 1000:
            raise ValueError("max_prompt_tokens must be >= 1000")
        if self.keep_recent_messages < 2:
            raise ValueError("keep_recent_messages must be >= 2")
        if self.repeat_warn_threshold < 2:
            raise ValueError("repeat_warn_threshold must be >= 2")
        if self.repeat_stop_threshold <= self.repeat_warn_threshold:
            raise ValueError(
                "repeat_stop_threshold must be > repeat_warn_threshold"
            )
        if self.max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be >= 0")


class AgentLoop:
    def __init__(
        self,
        llm: LLMClient,
        sandbox: Sandbox,
        tools: ToolRegistry,
        config: LoopConfig,
        trajectory_path: Path,
    ) -> None:
        self._llm = llm
        self._sandbox = sandbox
        self._tools = tools
        self._config = config
        self._trajectory_path = trajectory_path
        self._trajectory: TrajectoryWriter | None = None
        self._total_usage = Usage()
        self._steps_completed = 0
        self._repair_attempts = 0
        self._context = ContextManager(
            ContextConfig(
                max_prompt_tokens = config.max_prompt_tokens,
                keep_recent_messages = config.keep_recent_messages,
            )
        )

    def run(self, task: str) -> RunResult:
        self._trajectory = TrajectoryWriter(self._trajectory_path)
        self._trajectory.write(
            {
                "type": "run_start",
                "task": task,
                "mode": self._config.mode,
                "max_steps": self._config.max_steps,
                "prompt_version": prompt_version(self._config.mode),
            }
        )
        try:
            return self._run(task)
        except Exception as exc:
            return self._finish(
                "error",
                self._steps_completed,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _run(self, task: str) -> RunResult:
        if self._config.mode == "tools":
            system_prompt = TOOLS_SYSTEM_PROMPT
            tool_schemas = self._tools.schemas()
            finish_hint = "call the submit tool"
        else:
            system_prompt = BASH_SYSTEM_PROMPT
            tool_schemas = None
            finish_hint = "reply with SUBMIT"

        messages: list[Message] = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": build_task_prompt(
                    task,
                    self._config.max_steps,
                    finish_hint,
                ),
            },
        ]
        recent_signatures: list[str] = []

        for step in range(1, self._config.max_steps + 1):
            messages, compact_info = self._context.maybe_compact(messages)
            if compact_info is not None:
                self._write_compact_event(step, compact_info)
            try:
                response = self._llm.chat(
                    messages,
                    tools=tool_schemas,
                    temperature=self._config.temperature,
                )
            except LLMProtocolError as exc:
                # 模型返回坏 JSON / 无法解析的响应：可恢复，喂回错误继续。
                self._write_protocol_error_event(step, exc)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Your previous response could not be parsed: {exc}. "
                            "Retry with a valid tool call or response format."
                        ),
                    }
                )
                self._steps_completed = step
                continue

            self._total_usage = self._total_usage + response.usage
            self._context.observe_usage(response.usage)
            content = response.content or ""
            self._write_llm_event(step, response, content)

            # 模型可能把 max_tokens 全花在推理上，正文为空、finish_reason=length。
            # 这不是动作，不能参与重复检测，也不能当成模型的错误决策。
            if (
                response.finish_reason == "length"
                and not content.strip()
                and not response.tool_calls
            ):
                self._write_truncated_event(step, response)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was truncated before it "
                            "produced an action. Be concise: reply with one "
                            "short bash code block, or SUBMIT."
                        ),
                    }
                )
                self._steps_completed = step
                continue

            if self._config.mode == "tools":
                messages.append(_assistant_tool_message(response))
                actions = actions_from_tool_calls(response.tool_calls)
                if not actions:
                    fallback = parse_bash_action(content)
                    if fallback.kind == "submit":
                        self._steps_completed = step
                        return self._finish("submitted", step)
                    self._write_none_action(step)
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "No tool call found. Use one of the provided "
                                "tools, or call submit when done."
                            ),
                        }
                    )
                    self._steps_completed = step
                    continue
            else:
                messages.append({"role": "assistant", "content": content})
                actions = [parse_bash_action(content)]

            for action in actions:
                arguments = dict(action.arguments)
                if action.name == "bash":
                    arguments.setdefault(
                        "timeout", self._config.command_timeout
                    )

                if action.kind == "none":
                    # 空动作不是模型的选择，不能计入“重复动作”止损。
                    signature = ""
                    repeat_count = 0
                else:
                    signature = _action_signature(action, arguments)
                    repeat_count = _repeat_count(recent_signatures, signature)
                    recent_signatures.append(signature)

                self._write_action_event(step, action, arguments)

                if action.kind == "submit" or action.name == "submit":
                    if self._config.submit_check_command is None:
                        self._steps_completed = step
                        return self._finish("submitted", step)

                    check = self._sandbox.exec(self._config.submit_check_command,timeout=120)
                    check_output = truncate_middle(check.output, self._config.max_observation_chars)
                    passed = check.exit_code == 0
                    self._write_submit_check_event(
                        step,
                        passed,
                        check.exit_code,
                        check_output,
                    )
                    if passed:
                        self._steps_completed = step
                        return self._finish("submitted", step)

                    self._repair_attempts += 1
                    if self._repair_attempts > self._config.max_repair_attempts:
                        self._steps_completed = step
                        return self._finish(
                            "repair_failed",
                            step,
                            error=(
                                "visible tests still fail after "
                                f"{self._config.max_repair_attempts} "
                                "repair attempts"
                            ),
                        )

                    rejection = (
                        "Submission rejected: the repository's visible tests "
                        "failed. Fix the failures and submit again.\n\n"
                        f"{check_output}"
                    )
                    self._append_observation(messages, step, action, rejection)
                    self._write_observation_event(step, action, False, rejection)
                    break

                if repeat_count >= self._config.repeat_stop_threshold:
                    self._write_repeat_event(
                        step, action, arguments, repeat_count
                    )
                    self._steps_completed = step
                    return self._finish(
                        "looping",
                        step,
                        error=(
                            f"same action repeated {repeat_count} times: "
                            f"{signature}"
                        ),
                    )

                repeat_warning: str | None = None
                if repeat_count >= self._config.repeat_warn_threshold:
                    repeat_warning = (
                        f"Warning: you have repeated the same action "
                        f"{repeat_count} times. Inspect the observation and "
                        "try a different approach."
                    )

                if action.kind == "tool" and action.name:
                    tool_result = self._tools.execute(
                        action.name,
                        arguments,
                        self._sandbox,
                    )
                    observation = tool_result.output
                    ok = tool_result.ok
                else:
                    observation = (
                        "No action found. Use one of the provided tools, "
                        "or call submit when done."
                    )
                    ok = False

                prefixes: list[str] = list(action.notes)
                if repeat_warning:
                    prefixes.append(repeat_warning)
                if prefixes:
                    observation = "\n".join(prefixes) + "\n\n" + observation

                observation = truncate_middle(
                    observation,
                    self._config.max_observation_chars,
                )

                self._append_observation(
                    messages,
                    step,
                    action,
                    observation,
                )
                self._write_observation_event(
                    step,
                    action,
                    ok,
                    observation,
                )

            self._steps_completed = step

        return self._finish("max_steps", self._config.max_steps)

    def _append_observation(
        self,
        messages: list[Message],
        step: int,
        action: Action,
        observation: str,
    ) -> None:
        if self._config.mode == "tools":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": action.tool_call_id,
                    "content": observation,
                }
            )
        else:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Observation (step {step}/{self._config.max_steps}):\n"
                        f"{observation}"
                    ),
                }
            )

    def _write_llm_event(
        self,
        step: int,
        response: LLMResponse,
        content: str,
    ) -> None:
        if self._trajectory is None:
            return
        self._trajectory.write(
            {
                "type": "llm",
                "step": step,
                "model": response.model,
                "content": content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                    }
                    for call in response.tool_calls
                ],
                "finish_reason": response.finish_reason,
                "latency_ms": response.latency_ms,
                "usage": asdict(response.usage),
            }
        )

    def _write_action_event(
        self,
        step: int,
        action: Action,
        arguments: dict[str, Any],
    ) -> None:
        if self._trajectory is None:
            return
        self._trajectory.write(
            {
                "type": "action",
                "step": step,
                "kind": action.kind,
                "name": action.name,
                "arguments": arguments,
                "tool_call_id": action.tool_call_id,
                "notes": list(action.notes),
            }
        )

    def _write_none_action(self, step: int) -> None:
        if self._trajectory is None:
            return
        self._trajectory.write(
            {
                "type": "action",
                "step": step,
                "kind": "none",
                "name": None,
                "arguments": {},
                "tool_call_id": None,
                "notes": [],
            }
        )
        self._trajectory.write(
            {
                "type": "observation",
                "step": step,
                "name": None,
                "tool_call_id": None,
                "ok": False,
                "output": (
                    "No tool call found. Use one of the provided tools, "
                    "or call submit when done."
                ),
            }
        )

    def _write_observation_event(
        self,
        step: int,
        action: Action,
        ok: bool,
        observation: str,
    ) -> None:
        if self._trajectory is None:
            return
        self._trajectory.write(
            {
                "type": "observation",
                "step": step,
                "name": action.name,
                "tool_call_id": action.tool_call_id,
                "ok": ok,
                "output": observation,
            }
        )

    def _write_truncated_event(
        self,
        step: int,
        response: LLMResponse,
    ) -> None:
        if self._trajectory is None:
            return
        self._trajectory.write(
            {
                "type": "truncated",
                "step": step,
                "finish_reason": response.finish_reason,
                "completion_tokens": response.usage.completion_tokens,
            }
        )

    def _write_protocol_error_event(
            self,
            step: int,
            exc: Exception,
    ) -> None:
        if self._trajectory is None:
            return
        self._trajectory.write(
            {
                "type": "protocol_error",
                "step": step,
                "error": str(exc),
            }
        )

    def _write_repeat_event(
            self,
            step: int,
            action: Action,
            arguments: dict[str, Any],
            count: int,
    ) -> None:
        if self._trajectory is None:
            return
        self._trajectory.write(
            {
                "type": "repeat",
                "step": step,
                "name": action.name,
                "arguments": arguments,
                "count": count,
                "action": "stop",
            }
        )

    def _write_compact_event(self, step: int, info: CompactionInfo) -> None:
        if self._trajectory is None:
            return
        self._trajectory.write(
            {
                "type": "compact",
                "step": step,
                "before_messages": info.before_messages,
                "after_messages": info.after_messages,
                "before_tokens_est": info.before_tokens_est,
                "after_tokens_est": info.after_tokens_est,
                "last_prompt_tokens": info.last_prompt_tokens,
            }
        )

    def _write_submit_check_event(
            self,
            step: int,
            passed:bool,
            exit_code: int,
            output: str,
    ) -> None:
        if self._trajectory is None:
            return
        self._trajectory.write(
            {
                "type": "submit_check",
                "step": step,
                "passed": passed,
                "exit_code": exit_code,
                "output": output,
            }
        )

    def _finish(
        self,
        status: str,
        steps: int,
        error: str | None = None,
    ) -> RunResult:
        patch = collect_patch(self._sandbox)
        if self._trajectory is not None:
            self._trajectory.write(
                {
                    "type": "run_end",
                    "status": status,
                    "steps": steps,
                    "error": error,
                    "patch_chars": len(patch),
                    "usage": asdict(self._total_usage),
                }
            )
            self._trajectory.close()
        return RunResult(
            status=status,
            steps=steps,
            patch=patch,
            usage=self._total_usage,
            trajectory_path=self._trajectory_path,
            error=error,
        )


def _assistant_tool_message(response: LLMResponse) -> Message:
    message: Message = {
        "role": "assistant",
        "content": response.content or "",
    }
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": call.raw_arguments,
                },
            }
            for call in response.tool_calls
        ]
    return message


def _action_signature(action: Action, arguments: dict[str, Any]) -> str:
    try:
        payload = json.dumps(
            arguments,
            sort_keys=True,
            ensure_ascii=False,
        )
    except TypeError:
        payload = repr(arguments)
    return f"{action.name}:{payload}"


def _repeat_count(signatures: list[str], signature: str) -> int:
    count = 0
    for previous in reversed(signatures):
        if previous != signature:
            break
        count += 1
    return count + 1


def collect_patch(sandbox: Sandbox) -> str:
    """收集 agent 的修改，包括新增文件。"""
    try:
        root = sandbox.exec("git rev-list --max-parents=0 HEAD", timeout=10)
        if root.exit_code != 0:
            return ""
        baseline = root.stdout.strip().splitlines()
        if not baseline:
            return ""

        result = sandbox.exec(
            f"git add -A -N && git diff --binary {baseline[0]}",
            timeout=30,
        )
        if result.exit_code != 0:
            return ""
        return result.stdout
    except Exception:
        return ""
