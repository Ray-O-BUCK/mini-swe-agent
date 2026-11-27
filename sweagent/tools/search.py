"""search 工具：用 ripgrep 搜索仓库。"""
from __future__ import annotations

import shlex
from typing import Any

from ..sandbox.base import Sandbox
from ..types import ToolResult
from ..utils import truncate_middle
from .base import ToolError

MAX_RESULTS = 50
MAX_OUTPUT_CHARS = 12_000


class SearchTool:
    name = "search"
    description = (
        "Search the repository with ripgrep. Pattern is a regex. Returns "
        "path:line:match. Use it to locate symbols before viewing files."
    )
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression to search for.",
            },
            "path": {
                "type": "string",
                "description": "Relative path to search. Default '.'.",
            },
            "glob": {
                "type": "string",
                "description": "Optional ripgrep glob, e.g. '*.py'.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum result lines. Default 50, max 200.",
                "minimum": 1,
                "maximum": 200,
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    }

    def run(self, args: dict[str, Any], sandbox: Sandbox) -> ToolResult:
        pattern = args["pattern"]
        path = args.get("path", ".")
        glob = args.get("glob")
        max_results = int(args.get("max_results", MAX_RESULTS))
        if not pattern:
            raise ToolError("pattern must be non-empty")
        if not 1 <= max_results <= 200:
            raise ToolError(f"max_results must be between 1 and 200, got {max_results}")
        _ensure_relative(path)

        command = [
            "rg",
            "-n",
            "--no-heading",
            "--color",
            "never",
            "--max-columns",
            "300",
            "--max-columns-preview",
        ]

        if glob:
            command.extend(["-g", glob])
        command.extend(["--", pattern, path])

        result = sandbox.exec(
            " ".join(shlex.quote(part) for part in command),
            timeout = 30,
        )
        if result.exit_code == 1:
            return ToolResult(ok = True, output = f"no matches for {pattern!r}")
        if result.exit_code != 0:
            return ToolResult(
                ok = False,
                output = (
                    f"search failed with exit_code {result.exit_code}\n"
                    f"{result.stderr or result.stdout}"
                ),
            )

        lines = [
            _normalize_match_line(line)
            for line in result.stdout.splitlines()
        ]
        total = len(lines)
        if total > max_results:
            extra = total - max_results
            lines = lines[:max_results] + [f"... ({extra} more matches)"]
        output = truncate_middle("\n".join(lines), MAX_OUTPUT_CHARS)
        return ToolResult(ok = True, output = output, metadata = {"matches": total})





def _ensure_relative(path: str) -> None:
    normalized = path.replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized:
        raise ToolError("search path must be relative to the repository")
    if ".." in normalized.split("/"):
        raise ToolError("search path must not contain '..'")


def _normalize_match_line(line: str) -> str:
    r"""把 rg 输出里的路径分隔符统一成 /。

    只处理第一个冒号之前的路径，避免动到匹配内容里的反斜杠。
    Windows 下 rg 输出 `.\pkg\mod.py:1:...`，模型后面要把路径交给
    view/str_replace，正斜杠在两个平台都能用。
    """
    parts = line.split(":", 2)
    if len(parts) >= 2:
        parts[0] = parts[0].replace("\\", "/")
    return ":".join(parts)


