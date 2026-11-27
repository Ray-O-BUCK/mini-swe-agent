"""Single-task evaluation: apply a model patch and run hidden tests."""
from __future__ import annotations

import shlex
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from ..sandbox import DEFAULT_DOCKER_IMAGE, create_sandbox
from ..sandbox.base import Sandbox
from ..types import ExecResult
from ..utils import truncate_middle
from ..workspace import prepare_workspace_from_commit
from .spec import TaskSpec

MAX_OUTPUT_CHARS = 20_000


@dataclass(frozen=True)
class EvalResult:
    task_id: str
    resolved: bool
    patch_applied: bool
    test_patch_applied: bool
    exit_code: int
    failure_type: str
    output: str
    workspace: str
    sandbox: str
    duration_s: float


def apply_patch(sandbox: Sandbox, patch_path: str | Path) -> ExecResult:
    """在 workspace 里应用一个 patch 文件。"""
    return sandbox.exec(
        f"git apply --binary --verbose {shlex.quote(str(patch_path))}",
        timeout=30,
    )


def run_node_ids(sandbox: Sandbox, task: TaskSpec, node_ids: Sequence[str]) -> ExecResult:
    """运行 pytest 指定的 node id。"""
    command = task.test_command
    if node_ids:
        command += " " + " ".join(
            shlex.quote(node) for node in node_ids
        )
    return sandbox.exec(command, timeout=120)


def reset_test_files(sandbox: Sandbox, workspace: Path, task: TaskSpec) -> None:
    """把任务声明的测试文件恢复到 baseline。"""
    root = sandbox.exec("git rev-list --max-parents=0 HEAD", timeout=10)
    if root.exit_code != 0:
        raise RuntimeError(f"cannot find baseline commit: {root.output}")
    baseline = root.stdout.strip().splitlines()
    if not baseline:
        raise RuntimeError("baseline commit is empty")

    workspace = workspace.resolve()
    for path in task.test_files:
        quoted = shlex.quote(path)
        tracked = sandbox.exec(
            f"git ls-files --error-unmatch {quoted}",
            timeout=10,
        )
        if tracked.exit_code == 0:
            checkout = sandbox.exec(
                f"git checkout {baseline[0]} -- {quoted}",
                timeout=10,
            )
            if checkout.exit_code != 0:
                raise RuntimeError(
                    f"cannot reset {path!r}: {checkout.output}"
                )
        else:
            target = (workspace / path).resolve()
            if target != workspace and workspace not in target.parents:
                raise RuntimeError(f"test file escapes workspace: {path}")
            if target.is_file():
                target.unlink()


def evaluate_patch(task: TaskSpec, patch_text: str, sandbox_kind: str = "local", docker_image : str = DEFAULT_DOCKER_IMAGE) -> EvalResult:
    """在 fresh base workspace 上评测一个 model patch。"""
    patch_text = patch_text.replace("\r\n", "\n")
    started = time.perf_counter()
    root = Path(tempfile.mkdtemp(prefix=f"swe-eval-{task.id}-"))
    workspace = root / "workspace"
    patch_applied = False
    test_patch_applied = False

    if not patch_text.strip():
        return _make_result(
            task,
            workspace,
            started,
            sandbox=sandbox_kind,
            patch_applied=False,
            test_patch_applied=False,
            exit_code=-1,
            failure_type="no_patch",
            output="model patch is empty",
        )

    try:
        prepare_workspace_from_commit(task.repo, task.base_commit, workspace)
        sandbox = create_sandbox(workspace, kind = sandbox_kind, image = docker_image)
    except Exception as exc:
        return _make_result(
            task,
            workspace,
            started,
            sandbox=sandbox_kind,
            patch_applied=False,
            test_patch_applied=False,
            exit_code=-1,
            failure_type="error",
            output=f"{type(exc).__name__}: {exc}",
        )

    try:
        # patch 放进 workspace 内部：Docker 只挂载 workspace，
        # 容器里的 git 只能看到这个目录。
        patch_path = workspace / ".sweagent_model.patch"
        patch_path.write_text(patch_text, encoding="utf-8", newline="")
        applied = apply_patch(sandbox, ".sweagent_model.patch")
        if applied.exit_code != 0:
            return _make_result(
                task,
                workspace,
                started,
                sandbox=sandbox_kind,
                patch_applied=False,
                test_patch_applied=False,
                exit_code=applied.exit_code,
                failure_type="patch_apply_failed",
                output=applied.output,
            )
        patch_applied = True

        try:
            reset_test_files(sandbox, workspace, task)
        except Exception as exc:
            return _make_result(
                task,
                workspace,
                started,
                sandbox=sandbox_kind,
                patch_applied=True,
                test_patch_applied=False,
                exit_code=-1,
                failure_type="test_reset_failed",
                output=f"{type(exc).__name__}: {exc}",
            )

        hidden_patch = workspace / ".sweagent_hidden.patch"
        hidden_patch.write_bytes(task.test_patch.read_bytes().replace(b"\r\n", b"\n"))
        test_applied = apply_patch(sandbox, ".sweagent_hidden.patch")
        if test_applied.exit_code != 0:
            return _make_result(
                task,
                workspace,
                started,
                sandbox=sandbox_kind,
                patch_applied=True,
                test_patch_applied=False,
                exit_code=test_applied.exit_code,
                failure_type="test_patch_apply_failed",
                output=test_applied.output,
            )
        test_patch_applied = True

        result = run_node_ids(sandbox, task, task.fail_to_pass + task.pass_to_pass,)
        resolved = result.exit_code == 0
        return _make_result(
            task,
            workspace,
            started,
            sandbox=sandbox_kind,
            patch_applied=True,
            test_patch_applied=True,
            exit_code=result.exit_code,
            failure_type="resolved" if resolved else "test_fail",
            output=result.output,
        )
    except Exception as exc:
        return _make_result(
            task,
            workspace,
            started,
            sandbox=sandbox_kind,
            patch_applied=patch_applied,
            test_patch_applied=test_patch_applied,
            exit_code=-1,
            failure_type="error",
            output=f"{type(exc).__name__}: {exc}",
        )
    finally:
        sandbox.close()


def _make_result(
    task: TaskSpec,
    workspace: Path,
    started: float,
    *,
    sandbox: str,
    patch_applied: bool,
    test_patch_applied: bool,
    exit_code: int,
    failure_type: str,
    output: str,
) -> EvalResult:
    return EvalResult(
        task_id=task.id,
        resolved=failure_type == "resolved",
        patch_applied=patch_applied,
        test_patch_applied=test_patch_applied,
        exit_code=exit_code,
        failure_type=failure_type,
        output=truncate_middle(output, MAX_OUTPUT_CHARS),
        workspace=str(workspace),
        sandbox=sandbox,
        duration_s=round(time.perf_counter() - started, 3),
    )
