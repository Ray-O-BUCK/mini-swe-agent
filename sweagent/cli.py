"""命令行入口。

S1 只需要 `smoke`。后续 session 再加 `run`、`eval`、`report`、`replay`。
"""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, load_config, PROJECT_ROOT
from .llm import LLMClient, LLMError

import json
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .agent.loop import AgentLoop, LoopConfig
from .agent.prompt import prompt_version
from .sandbox import DEFAULT_DOCKER_IMAGE
from .sandbox.local import LocalSandbox
from .tools import default_registry
from .utils import truncate_middle
from .workspace import WorkspaceError, prepare_workspace, prepare_workspace_from_commit
from .benchmark.evaluate import evaluate_patch
from .benchmark.runner import BenchConfig, load_tasks, run_benchmark
from .benchmark.spec import TaskSpecError, load_task


SMOKE_PROMPT = "Reply with exactly: SWE-agent smoke test OK"

WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name, for example Beijing.",
                }
            },
            "required": ["city"],
            "additionalProperties": False,
        },
    },
}

def _cmd_smoke(args: argparse.Namespace) -> int:
    config = load_config()
    client = LLMClient(config.llm)

    print(f"model: {config.llm.model}")
    print(f"base_url: {config.llm.base_url}")

    print("--- 普通 chat ---")
    plain = client.chat(
        [{"role": "user", "content": args.prompt or SMOKE_PROMPT}]
    )
    print(f"content: {plain.content!r}")
    print(
        "usage: "
        f"prompt={plain.usage.prompt_tokens} "
        f"completion={plain.usage.completion_tokens} "
        f"total={plain.usage.total_tokens}"
    )
    print(f"latency_ms: {plain.latency_ms}")
    print(f"finish_reason: {plain.finish_reason}")

    print("--- tool call ---")
    tool_response = client.chat(
        [
            {
                "role": "system",
                "content": "Use the provided tool when the user asks about weather.",
            },
            {
                "role": "user",
                "content": "What is the weather in Beijing? Use the get_weather tool.",
            },
        ],
        tools=[WEATHER_TOOL],
    )
    if not tool_response.tool_calls:
        print("模型没有返回 tool call")
        print(f"content: {tool_response.content!r}")
        print(f"finish_reason: {tool_response.finish_reason}")
        return 2

    for call in tool_response.tool_calls:
        print(f"tool: {call.name} args={call.arguments}")
        print(f"raw_arguments: {call.raw_arguments!r}")
    print(
        "usage: "
        f"prompt={tool_response.usage.prompt_tokens} "
        f"completion={tool_response.usage.completion_tokens} "
        f"total={tool_response.usage.total_tokens}"
    )
    print("smoke: OK")
    return 0


def _default_run_dir(project_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    runs = project_root / "runs"
    candidate = runs / stamp
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = runs / f"{stamp}-{suffix}"
    return candidate


def _cmd_run(args: argparse.Namespace) -> int:
    config = load_config()
    repo = Path(args.repo).expanduser().resolve()

    if args.out:
        run_dir = Path(args.out).expanduser().resolve()
    else:
        run_dir = _default_run_dir(config.project_root)

    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise WorkspaceError(
            f"run directory already exists: {run_dir}"
        ) from exc

    # workspace 放系统临时目录：
    # - 避免 pytest 向上找到项目的 pyproject.toml；
    # - 避免 agent 通过 .. 读到项目根目录的 .env。
    workspace_root = Path(tempfile.mkdtemp(prefix="swe-agent-"))
    workspace = workspace_root / "workspace"
    if args.base_commit:
        prepare_workspace_from_commit(
            repo,
            args.base_commit,
            workspace,
        )
    else:
        prepare_workspace(repo, workspace)

    sandbox = LocalSandbox(workspace)
    try:
        registry = default_registry()
        agent = AgentLoop(
            llm=LLMClient(config.llm),
            sandbox=sandbox,
            tools=registry,
            config=LoopConfig(
                max_steps=args.max_steps,
                command_timeout=args.timeout,
                mode=args.mode,
                max_observation_chars=args.max_observation_chars,
                max_prompt_tokens=args.max_prompt_tokens,
                submit_check_command=args.submit_check_command,
            ),
            trajectory_path=run_dir / "trajectory.jsonl",
        )
        result = agent.run(args.task)
    finally:
        sandbox.close()

    (run_dir / "patch.diff").write_text(
        result.patch,
        encoding="utf-8",
        newline="",
    )
    payload = {
        "status": result.status,
        "steps": result.steps,
        "error": result.error,
        "usage": asdict(result.usage),
        "model": config.llm.model,
        "mode": args.mode,
        "prompt_version": prompt_version(args.mode),
        "max_steps": args.max_steps,
        "workspace": str(workspace),
        "base_commit": args.base_commit,
    }
    (run_dir / "result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )

    print(f"run_dir: {run_dir}")
    print(f"mode: {args.mode}")
    print(f"workspace: {workspace}")
    print(f"status: {result.status}")
    print(f"steps: {result.steps}")
    print(
        "usage: "
        f"prompt={result.usage.prompt_tokens} "
        f"completion={result.usage.completion_tokens} "
        f"total={result.usage.total_tokens}"
    )
    print(f"patch: {run_dir / 'patch.diff'} ({len(result.patch)} chars)")
    print(f"trajectory: {run_dir / 'trajectory.jsonl'}")
    if result.error:
        print(f"error: {result.error}")
    return 0 if result.status == "submitted" else 1


def _cmd_replay(args: argparse.Namespace) -> int:
    path = Path(args.trajectory).expanduser().resolve()
    if not path.is_file():
        print(f"trajectory not found: {path}", file=sys.stderr)
        return 1

    events = 0
    lines = path.read_text(encoding="utf-8").splitlines()
    for line_no, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[line {line_no}] invalid JSON: {exc}")
            continue
        _print_event(event, args.max_chars)
        events += 1
    print(f"--- {events} events ---")
    return 0


def _print_event(event:dict, max_chars: int) -> None:
    event_type = event.get("type", "?")
    step = event.get("step")
    prefix = f"[step {step}] " if step is not None else ""

    if event_type == "run_start":
        print(
            "=== run_start: "
            f"mode={event.get('mode')} "
            f"max_steps={event.get('max_steps')} "
            f"prompt={event.get('prompt_version')} ==="
        )
        print(f"task: {event.get('task')}")
    elif event_type == "llm":
        print(f"--- {prefix}LLM ({event.get('model')}) ---")
        content = event.get("content") or ""
        if content:
            print(truncate_middle(content, max_chars))
        for call in event.get("tool_calls", []):
            print(f"  tool_call: {call.get('name')} {call.get('arguments')}")
        print(f"  usage: {event.get('usage')}")
    elif event_type == "action":
        print(f"--- {prefix}ACTION {event.get('name')} ---")
        print(
            json.dumps(
                event.get("arguments", {}),
                ensure_ascii=False,
            )
        )
    elif event_type == "observation":
        print(f"--- {prefix}OBSERVATION ok={event.get('ok')} ---")
        print(truncate_middle(event.get("output") or "", max_chars))
    elif event_type == "protocol_error":
        print(f"--- {prefix}PROTOCOL ERROR ---")
        print(event.get("error"))
    elif event_type == "repeat":
        print(
            f"--- {prefix}REPEAT count={event.get('count')} "
            f"action={event.get('action')} ---"
        )
    elif event_type == "run_end":
        print(
            "=== run_end: "
            f"status={event.get('status')} "
            f"steps={event.get('steps')} "
            f"error={event.get('error')} "
            f"patch_chars={event.get('patch_chars')} ==="
        )
    else:
        print(f"--- {prefix}{event_type} ---")


def _cmd_eval(args: argparse.Namespace) -> int:
    task = load_task(Path(args.task))
    patch_path = Path(args.patch).expanduser().resolve()
    if not patch_path.is_file():
        print(f"patch not found: {patch_path}", file=sys.stderr)
        return 1

    # newline="" 保留 patch 的原始换行；CRLF 仓库的 patch 不能被转成 LF。
    with patch_path.open("r", encoding="utf-8", newline="") as handle:
        patch_text = handle.read()
    result = evaluate_patch(task, patch_text, sandbox_kind = args.sandbox, docker_image = args.docker_image)

    if args.out:
        out_dir = Path(args.out).expanduser().resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = PROJECT_ROOT / "runs" / "eval" / f"{task.id}-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "model.patch").write_text(
        patch_text,
        encoding="utf-8",
        newline="",
    )
    (out_dir / "result.json").write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )

    print(f"task: {result.task_id}")
    print(f"resolved: {result.resolved}")
    print(f"patch_applied: {result.patch_applied}")
    print(f"test_patch_applied: {result.test_patch_applied}")
    print(f"exit_code: {result.exit_code}")
    print(f"failure_type: {result.failure_type}")
    print(f"workspace: {result.workspace}")
    print(f"sandbox: {result.sandbox}")
    print(f"result: {out_dir / 'result.json'}")
    if not result.resolved:
        print("--- output ---")
        print(result.output)
    return 0 if result.resolved else 1


def _cmd_bench(args:argparse.Namespace) -> int:
    config = load_config()
    tasks_dir = Path(args.tasks).expanduser().resolve()
    try:
        tasks = load_tasks(tasks_dir)
    except TaskSpecError as exc:
        print(f"task error: {exc}", file=sys.stderr)
        return 1
    if not tasks:
        print(f"no tasks found under {tasks_dir}", file=sys.stderr)
        return 1

    if args.out:
        out_dir = Path(args.out).expanduser().resolve()
    else:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_dir = PROJECT_ROOT / "runs" / "bench" / stamp

    bench_config = BenchConfig(
        max_steps=args.max_steps,
        mode=args.mode,
        sandbox=args.sandbox,
        docker_image=args.docker_image,
        limit=args.limit,
        max_observation_chars=args.max_observation_chars,
        max_prompt_tokens=args.max_prompt_tokens,
        self_repair=args.self_repair,
        max_repair_attempts=args.max_repair_attempts,
    )
    results = run_benchmark(tasks, bench_config, config.llm, out_dir)
    resolved = sum(1 for result in results if result.eval_result.resolved)
    print(f"resolved: {resolved}/{len(results)}")
    print(f"report: {out_dir / 'report.md'}")
    return 0 if resolved == len(results) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sweagent",
        description="Mini SWE-agent CLI",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)


    smoke = subparsers.add_parser(
        "smoke",
        help="检查 API 凭据和 function calling 是否可用。",
    )
    smoke.add_argument(
        "--prompt",
        default=None,
        help="覆盖普通 chat 的默认 prompt。",
    )
    smoke.set_defaults(func=_cmd_smoke)


    run = subparsers.add_parser("run", help="Run the agent on a task.")
    run.add_argument("--repo", required=True, help="Source repository path.")
    run.add_argument("--task", required=True, help="Issue/task description.")
    run.add_argument(
        "--mode",
        choices=("bash", "tools"),
        default="tools",
        help="Action protocol: bash-only text or native tool calls.",
    )

    run.add_argument(
        "--base-commit",
        default=None,
        help=(
            "Create the agent workspace from this git commit. "
            "Use the task's base_commit for benchmark runs."
        ),
    )

    run.add_argument(
        "--max-observation-chars",
        type=int,
        default=12_000,
        help="Truncate each observation to this many characters.",
    )
    run.add_argument(
        "--max-prompt-tokens",
        type=int,
        default=None,
        help="Compact history when the last prompt exceeds this size.",
    )


    run.add_argument(
        "--max-steps", type=int, default=10, help="Maximum agent steps."
    )
    run.add_argument(
        "--timeout", type=int, default=60, help="Per-command timeout in seconds."
    )
    run.add_argument(
        "--out", default=None, help="Run directory (default: runs/<timestamp>)."
    )
    run.add_argument(
        "--submit-check-command",
        default=None,
        help="Command run before accepting SUBMIT; non-zero rejects it.",
    )
    run.set_defaults(func=_cmd_run)


    replay = subparsers.add_parser(
        "replay",
        help = "Print a trajectory in a readable form."
    )
    replay.add_argument("trajectory", help="Path to trajectory.jsonl")
    replay.add_argument(
        "--max-chars",
        type=int,
        default=2000,
        help="Max characters per content block.",
    )
    replay.set_defaults(func=_cmd_replay)


    eval_parser = subparsers.add_parser(
        "eval",
        help="Evaluate a model patch on a task.",
    )
    eval_parser.add_argument(
        "--task",
        required=True,
        help="Task directory or task.yaml.",
    )
    eval_parser.add_argument(
        "--patch",
        required=True,
        help="Model patch file.",
    )
    eval_parser.add_argument(
        "--sandbox",
        choices=("local", "docker"),
        default="local",
        help="Execution sandbox.",
    )
    eval_parser.add_argument(
        "--docker-image",
        default=DEFAULT_DOCKER_IMAGE,
        help="Docker image for --sandbox docker.",
    )
    eval_parser.add_argument(
        "--out",
        default=None,
        help="Output directory.",
    )
    eval_parser.set_defaults(func=_cmd_eval)


    bench = subparsers.add_parser(
        "bench",
        help="Run the agent + eval over a set of tasks.",
    )
    bench.add_argument(
        "--tasks",
        required=True,
        help="Directory containing task dirs.",
    )
    bench.add_argument(
        "--sandbox",
        choices=("local", "docker"),
        default="docker",
        help="Execution sandbox.",
    )
    bench.add_argument(
        "--docker-image",
        default=DEFAULT_DOCKER_IMAGE,
        help="Docker image for --sandbox docker.",
    )
    bench.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of tasks (for smoke runs).",
    )
    bench.add_argument(
        "--max-steps",
        type=int,
        default=10,
        help="Maximum agent steps per task.",
    )
    bench.add_argument(
        "--mode",
        choices=("bash", "tools"),
        default="tools",
        help="Agent action protocol.",
    )
    bench.add_argument(
        "--out",
        default=None,
        help="Output directory (default: runs/bench/<timestamp>).",
    )
    bench.set_defaults(func=_cmd_bench)
    bench.add_argument(
        "--max-observation-chars",
        type=int,
        default=12_000,
        help="Truncate each observation to this many characters.",
    )
    bench.add_argument(
        "--max-prompt-tokens",
        type=int,
        default=None,
        help="Compact history when the last prompt exceeds this size.",
    )
    bench.add_argument(
        "--self-repair",
        action="store_true",
        help="Run visible tests before accepting a submit.",
    )
    bench.add_argument(
        "--max-repair-attempts",
        type=int,
        default=2,
        help="How many rejected submits to tolerate.",
    )



    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"LLM 错误：{exc}", file=sys.stderr)
        return 1
    except WorkspaceError as exc:
        print(f"workspace error: {exc}", file=sys.stderr)
        return 1
    except TaskSpecError as exc:
        print(f"task error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

