"""Benchmark runner: run the agent and evaluate it on a set of tasks."""
from __future__ import annotations

import json
import tempfile
import time
import shlex

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..agent.loop import AgentLoop, LoopConfig
from ..config import LLMConfig
from ..llm import LLMClient
from ..sandbox import DEFAULT_DOCKER_IMAGE, create_sandbox
from ..tools import default_registry
from ..types import Usage
from ..workspace import prepare_workspace_from_commit
from .evaluate import EvalResult, evaluate_patch
from .report import TaskRunResult, write_report
from .spec import TaskSpec, load_task


@dataclass(frozen=True)
class BenchConfig:
    max_steps: int = 10
    mode: str = "tools"
    sandbox: str = "docker"
    docker_image: str = DEFAULT_DOCKER_IMAGE
    limit: int | None = None
    self_repair: bool = False
    max_observation_chars: int = 12_000
    max_prompt_tokens: int | None = None
    max_repair_attempts: int = 2


def load_tasks(tasks_dir: Path) -> list[TaskSpec]:
    paths = sorted(Path(tasks_dir).rglob("task.yaml"))
    return [load_task(path) for path in paths]


def run_benchmark(tasks: list[TaskSpec], config: BenchConfig, llm_config: LLMConfig, out_dir: Path) -> list[TaskRunResult]:
    out_dir.mkdir(parents = True, exist_ok = True)
    selected = tasks[: config.limit] if config.limit else tasks
    results: list[TaskRunResult] = []

    for index, task in enumerate(selected, 1):
        print(f"[{index}/{len(selected)}] {task.id}")
        try:
            results.append(run_task(task, config, llm_config, out_dir))
        except Exception as exc:
            print(f"[{task.id}] error: {type(exc).__name__}: {exc}")
            results.append(_error_result(task, config, out_dir, exc))

    report_path = write_report(
        results,
        out_dir,
        {
            "model": llm_config.model,
            "mode": config.mode,
            "sandbox": config.sandbox,
            "docker_image": (
                config.docker_image
                if config.sandbox == "docker"
                else None
            ),
            "max_steps": config.max_steps,
            "tasks": len(results),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "self_repair": config.self_repair,
        },
    )
    print(f"report: {report_path}")
    return results


def run_task(task: TaskSpec, config: BenchConfig, llm_config: LLMConfig, out_dir: Path) -> TaskRunResult:
    task_dir = out_dir / task.id
    task_dir.mkdir(parents=True, exist_ok=True)

    agent_root = Path(tempfile.mkdtemp(prefix=f"swe-bench-{task.id}"))
    workspace = agent_root / "workspace"
    prepare_workspace_from_commit(task.repo, task.base_commit, workspace)

    started = time.perf_counter()
    sandbox = create_sandbox(workspace, kind = config.sandbox, image = config.docker_image)
    try:
        agent = AgentLoop(
            llm = LLMClient(llm_config),
            sandbox = sandbox,
            tools = default_registry(),
            config = LoopConfig(
                max_steps=config.max_steps,
                mode=config.mode,
                max_observation_chars=config.max_observation_chars,
                max_prompt_tokens=config.max_prompt_tokens,
                submit_check_command=(
                    _visible_test_command(task)
                    if config.self_repair
                    else None
                ),
                max_repair_attempts=config.max_repair_attempts,
            ),
            trajectory_path = task_dir / "trajectory.json",
        )
        run_result = agent.run(task.problem_statement)
    finally:
        sandbox.close()
    agent_duration = round(time.perf_counter() - started, 3)

    (task_dir / "patch.diff").write_text(
        run_result.patch,
        encoding="utf-8",
        newline="",
    )
    (task_dir / "agent.json").write_text(
        json.dumps(
            {
                "status": run_result.status,
                "steps": run_result.steps,
                "error": run_result.error,
                "usage": {
                    "prompt_tokens": run_result.usage.prompt_tokens,
                    "completion_tokens": run_result.usage.completion_tokens,
                    "total_tokens": run_result.usage.total_tokens,
                },
                "patch_chars": len(run_result.patch),
                "model": llm_config.model,
                "mode": config.mode,
                "base_commit": task.base_commit,
                "workspace": str(workspace),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )

    eval_result = evaluate_patch(task, run_result.patch, sandbox_kind = config.sandbox, docker_image = config.docker_image)
    (task_dir / "eval.json").write_text(
        json.dumps(
            {
                "resolved": eval_result.resolved,
                "patch_applied": eval_result.patch_applied,
                "test_patch_applied": eval_result.test_patch_applied,
                "exit_code": eval_result.exit_code,
                "failure_type": eval_result.failure_type,
                "sandbox": eval_result.sandbox,
                "duration_s": eval_result.duration_s,
                "workspace": eval_result.workspace,
                "output": eval_result.output,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )

    return TaskRunResult(
        task_id=task.id,
        agent_status=run_result.status,
        steps=run_result.steps,
        usage=run_result.usage,
        patch_chars=len(run_result.patch),
        eval_result=eval_result,
        run_dir=str(task_dir),
        duration_s=agent_duration,
    )


def _error_result(task: TaskSpec, config: BenchConfig, out_dir: Path, exc: Exception) -> TaskRunResult:
    task_dir = out_dir / task.id
    task_dir.mkdir(parents = True, exist_ok = True)
    eval_result = EvalResult(
        task_id=task.id,
        resolved=False,
        patch_applied=False,
        test_patch_applied=False,
        exit_code=-1,
        failure_type="bench_error",
        output=f"{type(exc).__name__}: {exc}",
        workspace="",
        sandbox=config.sandbox,
        duration_s=0.0,
    )
    return TaskRunResult(
        task_id=task.id,
        agent_status="error",
        steps=0,
        usage=Usage(),
        patch_chars=0,
        eval_result=eval_result,
        run_dir=str(task_dir),
        duration_s=0.0,
    )


def _visible_test_command(task: TaskSpec) -> str:
    """用 p2p 的函数名生成可见测试闸门命令。

    只选择仓库里已有的回归测试；hidden test 不在 agent workspace 里，
    也不能拿来当反馈（那是 oracle）。
    pytest exit 5 表示没有匹配到测试，视为闸门不可用、放行。
    """
    names: list[str] = []
    for node in task.pass_to_pass:
        function = node.split("::")[-1].split("[")[0]
        if function and function not in names:
            names.append(function)
    if not names:
        return task.test_command
    selector = "or".join(names)
    return (
        f"{task.test_command} -k {shlex.quote(selector)} "
        f"|| [ $? -eq 5 ]"
    )