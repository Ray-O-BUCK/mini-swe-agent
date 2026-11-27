"""Sandbox 接口。

Sandbox 把“在工作区里执行命令/读写文件”抽象出来。agent loop 和工具只依赖
这个 Protocol，因此 LocalSandbox 和 DockerSandbox 可以互换。
"""
from __future__ import annotations

from typing import Protocol

from ..types import ExecResult


class SandboxError(RuntimeError):
    """沙箱操作失败（路径越界、文件不存在、命令无法执行等）。"""


class Sandbox(Protocol):
    def exec(self, command: str, timeout: int = 60) -> ExecResult:
        """在工作区执行命令。超时通过 ExecResult.timed_out 表示。"""
        ...

    def read_text(self, path: str) -> str:
        """读取完整文本（保留换行符），用于编辑。"""
        ...

    def write_text(self, path: str, content: str) -> None:
        """写入完整文本（不做换行转换）。"""
        ...

    def exists(self, path: str) -> bool:
        """路径是否是工作区内的文件。"""
        ...

    def read_file(
        self,
        path: str,
        start: int | None = None,
        end: int | None = None,
    ) -> str:
        """按行读取；行号从 1 开始，end 包含在结果里。"""
        ...

    def write_file(self, path: str, content: str) -> None:
        """写入文件；必要时创建父目录。"""
        ...

    def close(self) -> None:
        """释放资源。LocalSandbox 是 no-op。"""
        ...
