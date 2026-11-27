"""Validate base/fix behavior of a benchmark task."""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from sweagent.benchmark.evaluate import apply_patch, run_node_ids
from sweagent.benchmark.spec import TaskSpec, TaskSpecError, load_task
from sweagent.sandbox import DEFAULT_DOCKER_IMAGE, create_sandbox
from sweagent.types import ExecResult
from sweagent.tools.bash import format_exec_result
from sweagent.workspace import WorkspaceError, prepare_workspace_from_commit


def _run_phase(
    task: TaskSpec,
    commit: str,
    label: str,
    sandbox_kind: str,
    docker_image: str,
) -> tuple[bool, ExecResult | None, ExecResult | None]:
    root = Path(tempfile.mkdtemp(prefix=f"swe-validate-{label}-"))
    workspace = root / "workspace"
    print(f"[{label}] workspace: {workspace}")

    try:
        prepare_workspace_from_commit(task.repo, commit, workspace)
    except WorkspaceError as exc:
        print(f"[{label}] workspace error: {exc}")
        return False, None, None

    test_patch_in_workspace = workspace / ".sweagent_validate_test.patch"
    test_patch_in_workspace.write_bytes(task.test_patch.read_bytes().replace(b"\r\n", b"\n"))
    assert test_patch_in_workspace.is_file(), test_patch_in_workspace

    sandbox = create_sandbox(workspace, kind = sandbox_kind, image = docker_image)

    should_apply_test_patch = label != "fix"
    if label == "fix":
        # fix commit 通常已经包含测试改动；反向 check 成功就跳过。
        reverse_check = sandbox.exec(
            "git apply --reverse --check .sweagent_validate_test.patch",
            timeout=30,
        )
        should_apply_test_patch = reverse_check.exit_code != 0

    try:
        if should_apply_test_patch:
            applied = apply_patch(
                sandbox, ".sweagent_validate_test.patch"
            )
            if applied.exit_code != 0:
                print(f"[{label}] test patch failed to apply")
                print(applied.output[-2000:])
                return False, None, None

        f2p = run_node_ids(sandbox, task, task.fail_to_pass)
        p2p = run_node_ids(sandbox, task, task.pass_to_pass)
    finally:
        sandbox.close()

    print(f"[{label}] fail_to_pass exit={f2p.exit_code}")
    print(f"[{label}] pass_to_pass exit={p2p.exit_code}")
    if f2p.exit_code != 0:
        print(format_exec_result(f2p)[-2000:])
    if p2p.exit_code != 0:
        print(format_exec_result(p2p)[-2000:])
    return True, f2p, p2p


def validate_task(task: TaskSpec, sandbox_kind: str, docker_image: str) -> bool:
    if not task.fix_commit:
        print("fix_commit is required for validation")
        return False

    ok, base_f2p, base_p2p = _run_phase(
        task, task.base_commit, "base",
        sandbox_kind, docker_image,
    )
    if not ok or base_f2p is None or base_p2p is None:
        return False
    if base_f2p.exit_code == 0:
        print("[base] fail_to_pass passed; bug not reproduced")
        return False
    if base_p2p.exit_code != 0:
        print("[base] pass_to_pass failed")
        return False

    ok, fix_f2p, fix_p2p = _run_phase(
        task, task.fix_commit, "fix",
        sandbox_kind, docker_image,
    )
    if not ok or fix_f2p is None or fix_p2p is None:
        return False
    if fix_f2p.exit_code != 0:
        print("[fix] fail_to_pass failed")
        return False
    if fix_p2p.exit_code != 0:
        print("[fix] pass_to_pass failed")
        return False

    print("task valid")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", help="task directory or YAML file")
    parser.add_argument(
        "--sandbox",
        choices=("local", "docker"),
        default="local",
    )
    parser.add_argument(
        "--docker-image",
        default=DEFAULT_DOCKER_IMAGE,
        
    )
    args = parser.parse_args()

    try:
        task = load_task(Path(args.task))
    except TaskSpecError as exc:
        print(f"task error: {exc}")
        return 1
    return 0 if validate_task(task, args.sandbox, args.docker_image) else 1


if __name__ == "__main__":
    raise SystemExit(main())
