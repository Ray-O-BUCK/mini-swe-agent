"""AgentLoop 的 FakeLLM 测试，不调用真实 API。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sweagent.agent.loop import AgentLoop, LoopConfig
from sweagent.llm import LLMError, LLMProtocolError
from sweagent.sandbox.local import LocalSandbox
from sweagent.tools import default_registry
from sweagent.types import LLMResponse, ToolCall, Usage


class FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def chat(self, messages, *, tools=None, temperature=0.0):
        self.calls.append([dict(message) for message in messages])
        if not self._responses:
            raise AssertionError("FakeLLM ran out of responses")
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _usage() -> Usage:
    return Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)


def _tool_response(
    name: str,
    arguments: dict,
    call_id: str = "call_1",
    content: str = "",
) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=[
            ToolCall(
                id=call_id,
                name=name,
                arguments=arguments,
                raw_arguments=json.dumps(arguments),
            )
        ],
        usage=_usage(),
        model="fake",
        finish_reason="tool_calls",
        latency_ms=1,
    )


def _text_response(content: str) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=[],
        usage=_usage(),
        model="fake",
        finish_reason="stop",
        latency_ms=1,
    )


def _make_loop(tmp_path: Path, llm: FakeLLM, **kwargs) -> AgentLoop:
    workspace = tmp_path / "workspace"
    sandbox = LocalSandbox(workspace)
    return AgentLoop(
        llm=llm,
        sandbox=sandbox,
        tools=default_registry(),
        config=LoopConfig(mode="tools", **kwargs),
        trajectory_path=tmp_path / "trajectory.jsonl",
    )


def _submit_response(call_id: str = "call_submit") -> LLMResponse:
    return _tool_response("submit", {}, call_id=call_id)


def test_max_steps(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            _tool_response("bash", {"command": "echo hi"})
            for _ in range(3)
        ]
    )
    loop = _make_loop(tmp_path, llm, max_steps=3)
    result = loop.run("task")
    assert result.status == "max_steps"
    assert result.steps == 3
    assert len(llm.calls) == 3


def test_protocol_error_is_fed_back(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            LLMProtocolError("invalid JSON in tool call"),
            _tool_response("submit", {}),
        ]
    )
    loop = _make_loop(tmp_path, llm, max_steps=3)
    result = loop.run("task")
    assert result.status == "submitted"
    assert result.steps == 2
    trajectory = (tmp_path / "trajectory.jsonl").read_text(encoding="utf-8")
    assert "protocol_error" in trajectory


def test_llm_error_sets_error_status(tmp_path: Path) -> None:
    llm = FakeLLM([LLMError("boom")])
    loop = _make_loop(tmp_path, llm, max_steps=3)
    result = loop.run("task")
    assert result.status == "error"
    assert "boom" in (result.error or "")


def test_repeated_action_stops(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            _tool_response(
                "bash",
                {"command": "echo hi"},
                call_id=f"call_{index}",
            )
            for index in range(5)
        ]
    )
    loop = _make_loop(
        tmp_path,
        llm,
        max_steps=10,
        repeat_warn_threshold=3,
        repeat_stop_threshold=5,
    )
    result = loop.run("task")
    assert result.status == "looping"
    assert result.steps == 5
    trajectory = (tmp_path / "trajectory.jsonl").read_text(encoding="utf-8")
    assert '"type": "repeat"' in trajectory
    assert "Warning: you have repeated" in json.dumps(
        llm.calls[-1],
        ensure_ascii=False,
    )


def test_observation_is_truncated(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            _tool_response(
                "bash",
                {"command": "python -c \"print('A' * 5000)\""},
            ),
            _tool_response("submit", {}),
        ]
    )
    loop = _make_loop(
        tmp_path,
        llm,
        max_steps=3,
        max_observation_chars=100,
    )
    result = loop.run("task")
    assert result.status == "submitted"

    second_call = llm.calls[1]
    tool_message = next(
        message for message in second_call if message["role"] == "tool"
    )
    assert len(tool_message["content"]) <= 100
    assert "中间输出已省略" in tool_message["content"]


def test_tool_error_is_fed_back(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            _tool_response("nope", {}),
            _tool_response("submit", {}),
        ]
    )
    loop = _make_loop(tmp_path, llm, max_steps=3)
    result = loop.run("task")
    assert result.status == "submitted"

    second_call = llm.calls[1]
    tool_message = next(
        message for message in second_call if message["role"] == "tool"
    )
    assert "未知工具" in tool_message["content"]


def test_submit_gate_passes(tmp_path: Path) -> None:
    llm = FakeLLM([_submit_response()])
    loop = _make_loop(
        tmp_path,
        llm,
        max_steps=3,
        submit_check_command="true",
    )
    result = loop.run("task")
    assert result.status == "submitted"


def test_submit_gate_rejects_then_accepts(tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            _submit_response("c1"),
            _tool_response(
                "bash", {"command": "touch ok.txt"}, call_id="c2"
            ),
            _submit_response("c3"),
        ]
    )
    loop = _make_loop(
        tmp_path,
        llm,
        max_steps=5,
        submit_check_command="test -f ok.txt",
    )
    result = loop.run("task")
    assert result.status == "submitted"

    trajectory = (tmp_path / "trajectory.jsonl").read_text(encoding="utf-8")
    assert trajectory.count('"type": "submit_check"') == 2
    assert '"passed": false' in trajectory
    assert '"passed": true' in trajectory


def test_submit_gate_gives_up(tmp_path: Path) -> None:
    llm = FakeLLM(
        [_submit_response(f"c{index}") for index in range(3)]
    )
    loop = _make_loop(
        tmp_path,
        llm,
        max_steps=5,
        submit_check_command="false",
        max_repair_attempts=1,
    )
    result = loop.run("task")
    assert result.status == "repair_failed"
    assert "repair attempts" in (result.error or "")


def test_truncated_response_is_nudged(tmp_path: Path) -> None:
    truncated = LLMResponse(
        content="",
        tool_calls=[],
        usage=_usage(),
        model="fake",
        finish_reason="length",
        latency_ms=1,
    )
    llm = FakeLLM([truncated, _tool_response("submit", {})])
    loop = _make_loop(tmp_path, llm, max_steps=3)
    result = loop.run("task")

    assert result.status == "submitted"
    trajectory = (tmp_path / "trajectory.jsonl").read_text(encoding="utf-8")
    assert '"type": "truncated"' in trajectory
    # 截断后的提示应该进入下一次调用
    assert any(
        "truncated" in str(message.get("content", ""))
        for message in llm.calls[1]
    )


def test_none_actions_do_not_trigger_looping(tmp_path: Path) -> None:
    llm = FakeLLM(
        [_text_response("no action here") for _ in range(3)]
    )
    loop = _make_loop(tmp_path, llm, max_steps=3)
    result = loop.run("task")

    assert result.status == "max_steps"
    assert result.status != "looping"