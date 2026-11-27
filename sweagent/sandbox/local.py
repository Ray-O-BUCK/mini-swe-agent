"""LocalSandbox：在宿主机的临时目录里执行命令。

重要：这不是安全边界。

`bash` 工具执行的是模型生成的任意 shell 命令，`_resolve` 只保护
read_file/write_file 这类结构化操作，保护不了 `rm -rf /` 或绝对路径。
它只适合运行你自己构造的可信任务/toy repo。正式评测必须用 DockerSandbox。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from ..types import ExecResult
from .base import SandboxError

from functools import lru_cache


# 传给子进程的环境变量白名单。绝不能把 LLM_API_KEY 之类的东西带进去。
_SAFE_ENV_KEYS = {
    "PATH",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "COMSPEC",
    "PATHEXT",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
    "VIRTUAL_ENV",
}


@lru_cache(maxsize=1)
def _find_git_bash() -> str | None:
    """优先找 Git for Windows 的 wrapper：<Git>/bin/bash.exe。

    不能直接用 shutil.which("bash")：PATH 里通常会先命中
    Git 的 usr/bin/bash.exe（raw MSYS bash），它在最小环境变量下不能
    正确执行 Windows 路径；更糟的是命中 C:/Windows/System32/bash.exe
    （WSL bash），它根本看不到 F: 盘。两者都会让 F:/.../python.exe
    报 "No such file or directory"。
    """
    git = shutil.which("git")
    if git:
        git_path = Path(git).resolve()
        # git 可能在 <Git>/cmd/git.exe 或 <Git>/mingw64/bin/git.exe
        for parent in git_path.parents:
            candidate = parent / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)

    # Git 不在 PATH 时的常见安装位置
    bases = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    for base in bases:
        if not base:
            continue
        for parts in (
            ("Git", "bin", "bash.exe"),
            ("Programs", "Git", "bin", "bash.exe"),
        ):
            candidate = Path(base).joinpath(*parts)
            if candidate.is_file():
                return str(candidate)
    return None


class LocalSandbox:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str | Path) -> Path:
        """把相对路径解析到工作区内；越界直接拒绝。

        注意：这只保护结构化文件操作，不保护任意 shell 命令。
        """
        candidate = (self._root / path).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise SandboxError(f"路径越界：{path!r} 不在工作区内")
        return candidate

    def _env(self) -> dict[str, str]:
        """只传白名单环境变量，避免 API key 泄漏给子进程。"""
        env = {
            key: value
            for key, value in os.environ.items()
            if key in _SAFE_ENV_KEYS
        }
        if os.name == "nt":
            # Git Bash 需要 MSYSTEM 才能正确初始化 PATH 和路径转换。
            env.setdefault("MSYSTEM", "MINGW64")
        return env

    def _build_command(self, command: str) -> tuple[str | list[str], bool]:
        """Windows 上优先用 Git Bash 的 wrapper，保证 Unix 命令语义。

        如果找不到 Git Bash，退回 cmd.exe；这时 ls、&& 等行为和 Linux
        不一样，属于 LocalSandbox 的已知限制。正式评测用 Docker。

        加 `set -o pipefail`：管道里任何一个命令失败，整条命令就失败。
        否则 `pytest | tail` 会把 pytest 的失败吞掉。
        """
        if os.name == "nt":
            bash = _find_git_bash()
            if bash:
                return [bash, "-lc", "set -o pipefail;" + command], False
        else:
            bash = shutil.which("bash")
            if bash:
                return [bash, "-lc", "set -o pipefail;" + command], False
        return command, True

    def read_text(self, path: str) -> str:
        """读取完整文本，保留原始换行符。"""
        target = self._resolve(path)
        if not target.is_file():
            raise SandboxError(f"文件不存在：{path!r}")
        data = target.read_bytes()
        if b"\x00" in data[:1024]:
            raise SandboxError(f"拒绝读取二进制文件：{path!r}")
        return data.decode("utf-8", errors="replace")

    def write_text(self, path: str, content: str) -> None:
        """写入完整文本，不做换行转换。"""
        target = self._resolve(path)
        if target == self._root or target.is_dir():
            raise SandboxError(f"不是文件路径：{path!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # newline="" 表示不做换行转换；content 里的 \n 原样写入。
        target.write_bytes(content.encode("utf-8"))

    def exists(self, path: str) -> bool:
        """路径是否是工作区内的文件。"""
        return self._resolve(path).is_file()

    def exec(self, command: str, timeout: int = 60) -> ExecResult:
        started = time.perf_counter()
        argv, use_shell = self._build_command(command)
        try:
            completed = subprocess.run(
                argv,
                shell=use_shell,
                cwd=self._root,
                capture_output=True,
                timeout=timeout,
                env=self._env(),
            )
            # 不用 text=True：它会开启 universal newlines，把子进程输出里的
            # CRLF 悄悄改成 LF。patch 收集必须保留原始字节的换行，否则
            # CRLF 仓库的 patch 应用不到 CRLF 的 base 上。
            return ExecResult(
                command=command,
                exit_code=completed.returncode,
                stdout=_as_text(completed.stdout),
                stderr=_as_text(completed.stderr),
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except subprocess.TimeoutExpired as exc:
            # 注意：这里只杀了直接子进程，grandchildren 可能残留。
            # DockerSandbox 会通过 kill 整个容器解决这个问题。
            return ExecResult(
                command=command,
                exit_code=-1,
                stdout=_as_text(exc.stdout),
                stderr=_as_text(exc.stderr),
                duration_ms=int((time.perf_counter() - started) * 1000),
                timed_out=True,
            )
        except OSError as exc:
            raise SandboxError(f"命令无法执行：{exc}") from exc

    def read_file(
        self,
        path: str,
        start: int | None = None,
        end: int | None = None,
    ) -> str:
        """按行读取；行号从 1 开始，end 包含。"""
        text = self.read_text(path)
        lines = text.splitlines()
        start_line = 1 if start is None else start
        if start_line < 1:
            raise SandboxError(f"start 必须 >= 1，当前是 {start_line}")
        if end is not None and end < start_line:
            raise SandboxError(f"end={end} 小于 start={start_line}")
        if start_line > len(lines):
            return ""
        end_line = len(lines) if end is None else min(end, len(lines))
        return "\n".join(lines[start_line - 1 : end_line])

    def write_file(self, path: str, content: str) -> None:
        """兼容旧接口，等价于 write_text。"""
        self.write_text(path, content)

    def close(self) -> None:
        # 工作区由调用方（runner）负责清理；这里不删，避免误删用户的目录。
        return None


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
