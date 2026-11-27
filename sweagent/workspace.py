"""Workspace preparation for agent runs and benchmark tasks."""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path


class WorkspaceError(RuntimeError):
    """无法准备工作区。"""


_IGNORE = shutil.ignore_patterns(
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".idea",
    ".vscode",
    "runs",
    "node_modules",
)

_TEXT_SUFFIXES = {
    ".py", ".pyi", ".md", ".txt", ".rst", ".toml", ".cfg",
    ".ini", ".yaml", ".yml", ".json", ".sh", ".html", ".css",
    ".js", ".ts", ".xml", ".csv",
}


def prepare_workspace(source: Path, destination: Path) -> None:
    """复制源仓库工作树，建立单 commit 基线（给 agent run 用）。"""
    source = source.resolve()
    destination = destination.resolve()

    if not source.is_dir():
        raise WorkspaceError(f"source repository does not exist: {source}")
    if source == destination or source in destination.parents:
        raise WorkspaceError(
            "run directory must not be inside the source repository"
        )
    if destination.exists():
        raise WorkspaceError(f"workspace already exists: {destination}")

    try:
        shutil.copytree(source, destination, ignore=_IGNORE)
    except OSError as exc:
        raise WorkspaceError(f"failed to copy repository: {exc}") from exc

    _normalize_text_files(destination)
    _init_baseline(destination)


def prepare_workspace_from_commit(
    repo: Path,
    commit: str,
    destination: Path,
) -> None:
    """从 git commit 的 archive 生成隔离 workspace（给评测用）。

    为什么用 archive 而不是工作树：
    - base_commit 可能不是当前 checkout；
    - archive 内容确定，不会把 .git 历史和未来 commit 带进 workspace。
    """
    repo = repo.resolve()
    destination = destination.resolve()

    if not repo.is_dir():
        raise WorkspaceError(f"repository does not exist: {repo}")
    if destination.exists():
        raise WorkspaceError(f"workspace already exists: {destination}")

    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", commit],
            check=True,
            capture_output=True,
            env=_git_env(),
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode(
            "utf-8", errors="replace"
        ).strip()
        raise WorkspaceError(
            f"git archive {commit!r} failed: {detail}"
        ) from exc

    destination.mkdir(parents=True, exist_ok=False)
    try:
        with tarfile.open(
            fileobj=io.BytesIO(completed.stdout),
            mode="r:",
        ) as tar:
            try:
                tar.extractall(destination, filter="data")
            except TypeError:
                # Python < 3.12 没有 filter 参数；只用于本地可信仓库。
                tar.extractall(destination)
        _normalize_text_files(destination)
        _init_baseline(destination)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _init_baseline(destination: Path) -> None:
    _git(destination, "init", "-q")
    _git(destination, "config", "user.email", "agent@example.com")
    _git(destination, "config", "user.name", "SWE Agent")
    _git(destination, "config", "core.autocrlf", "false")
    _git(destination, "add", "-A", "-f")
    _git(destination, "commit", "-q", "-m", "baseline")


def _git(cwd: Path, *args: str) -> None:
    try:
        subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_git_env(),
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise WorkspaceError(
            f"git {' '.join(args)} failed: {detail}"
        ) from exc
    except OSError as exc:
        raise WorkspaceError(f"failed to run git: {exc}") from exc


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("LLM_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        env.pop(key, None)
    return env


def _normalize_text_files(root: Path) -> None:
    """把文本文件统一成 LF，消除 Windows autocrlf 的影响。"""
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            continue
        data = path.read_bytes()
        if b"\x00" in data[:1024]:
            continue
        normalized = data.replace(b"\r\n", b"\n")
        if normalized != data:
            path.write_bytes(normalized)
