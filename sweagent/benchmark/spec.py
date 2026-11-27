"""TaskSpec：一个 SWE-bench 风格任务的定义与加载。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TaskSpecError(RuntimeError):
    """任务定义缺失或非法。"""


@dataclass(frozen=True)
class TaskSpec:
    id: str
    repo: Path
    base_commit: str
    fix_commit: str | None
    problem_statement: str
    test_command: str
    test_files: tuple[str, ...]
    fail_to_pass: tuple[str, ...]
    pass_to_pass: tuple[str, ...]
    test_patch: Path
    task_dir: Path


def load_task(path: str | Path) -> TaskSpec:
    """从目录或 task.yaml 加载任务。"""
    path = Path(path)
    task_file = path / "task.yaml" if path.is_dir() else path
    task_dir = task_file.parent
    if not task_file.is_file():
        raise TaskSpecError(f"task file not found: {task_file}")

    try:
        data = yaml.safe_load(task_file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TaskSpecError(f"invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TaskSpecError("task.yaml must be a mapping")

    required = [
        "id",
        "repo",
        "base_commit",
        "problem_statement",
        "test_files",
        "fail_to_pass",
        "pass_to_pass",
        "hidden",
    ]
    missing = [key for key in required if key not in data]
    if missing:
        raise TaskSpecError(f"missing fields: {missing}")

    for key in ("test_files", "fail_to_pass", "pass_to_pass"):
        if not isinstance(data[key], list):
            raise TaskSpecError(f"{key} must be a list")

    repo = Path(data["repo"])
    if not repo.is_absolute():
        repo = PROJECT_ROOT / repo
    repo = repo.resolve()

    hidden = data["hidden"]
    if not isinstance(hidden, dict) or "test_patch" not in hidden:
        raise TaskSpecError("hidden.test_patch is required")
    test_patch = (task_dir / hidden["test_patch"]).resolve()

    fail_to_pass = tuple(str(item) for item in data["fail_to_pass"])
    pass_to_pass = tuple(str(item) for item in data["pass_to_pass"])
    if not fail_to_pass:
        raise TaskSpecError("fail_to_pass must not be empty")
    if not repo.is_dir():
        raise TaskSpecError(f"repo not found: {repo}")
    if not test_patch.is_file():
        raise TaskSpecError(f"test patch not found: {test_patch}")

    return TaskSpec(
        id=str(data["id"]),
        repo=repo,
        base_commit=str(data["base_commit"]),
        fix_commit=(
            str(data["fix_commit"]) if data.get("fix_commit") else None
        ),
        problem_statement=str(data["problem_statement"]).strip(),
        test_command=str(
            data.get("test_command", "python -m pytest -q")
        ),
        test_files=tuple(str(item) for item in data["test_files"]),
        fail_to_pass=fail_to_pass,
        pass_to_pass=pass_to_pass,
        test_patch=test_patch,
        task_dir=task_dir.resolve(),
    )
