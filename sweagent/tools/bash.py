"""bash 工具：在工作区执行非交互式 shell 命令。"""
from __future__ import annotations

from typing import Any

from ..sandbox.base import Sandbox
from ..types import ExecResult, ToolResult
from ..utils import truncate_middle
from .base import ToolError

MAX_OUTPUT_CHARS = 12_000


class BashTool:
    name = "bash"
    description = (
        "在工作区执行一条非交互式 shell 命令。不要运行会等待输入的命令；"
        "需要长时间运行的命令请设置 timeout。输出会被截断。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "要执行的 shell 命令。",
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数，默认 60，范围 1..300。",
                "minimum": 1,
                "maximum": 300,
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def run(self, args: dict[str, Any], sandbox: Sandbox) -> ToolResult:
        command = args["command"]
        if not isinstance(command, str) or not command.strip():
            raise ToolError("command 必须是非空字符串")

        timeout = int(args.get("timeout", 60))
        if not 1 <= timeout <= 300:
            raise ToolError(f"timeout 必须在 1..300 秒内，当前是 {timeout}")

        result = sandbox.exec(command, timeout=timeout)
        return ToolResult(
            ok=result.exit_code == 0 and not result.timed_out,
            output=format_exec_result(result),
            metadata={
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "duration_ms": result.duration_ms,
            },
        )


def format_exec_result(
    result: ExecResult,
    max_chars: int = MAX_OUTPUT_CHARS,
) -> str:
    """把 ExecResult 变成模型能读的 observation。

    格式由 ExecResult.output 统一提供；这里只负责按工具预算截断。
    这样评测层、BashTool 和日志看到的是同一份文本格式。
    """
    return truncate_middle(result.output, max_chars)
