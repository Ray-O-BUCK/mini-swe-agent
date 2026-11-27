"""view 工具：带行号查看文件片段。"""
from __future__ import annotations

from typing import Any

from ..sandbox.base import Sandbox
from ..types import ToolResult
from ..utils import truncate_middle
from .base import ToolError

MAX_VIEW_LINES = 200
MAX_OUTPUT_CHARS = 12_000


class ViewTool:
    name = "view"
    description = (
        "View a text file with line numbers. start/end are 1-indexed and "
        "end-inclusive. At most 200 lines per call. The 'NN | ' prefix is "
        "display-only; do not include it in str_replace old text."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the repository root.",
            },
            "start": {
                "type": "integer",
                "description": "First line to view (1-indexed). Default 1.",
                "minimum": 1,
            },
            "end": {
                "type": "integer",
                "description": "Last line to view (inclusive).",
                "minimum": 1,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def run(self, args: dict[str, Any], sandbox: Sandbox) -> ToolResult:
        path = args['path']
        start = args.get('start')
        end = args.get('end')
        first = 1 if start is None else start
        if end is None:
            last = first + MAX_VIEW_LINES - 1
        else:
            last = min(end, first + MAX_VIEW_LINES - 1)
        if last < first:
            raise ToolError(f"end ({last}) must be >= start ({first})")

        content = sandbox.read_file(path, first, last)
        lines = content.splitlines()
        if not lines:
            return ToolResult(
                ok = True,
                output=f"{path}: no content in lines {first}-{last}",
                metadata = {"path": path, "start": first, "end": last}
            )

        numbered = "\n".join(
            f"{first + index:>4} | {line}"
            for index, line in enumerate(lines)
        )
        header = f"{path}: lines {first}-{first + len(lines) - 1}"
        output = truncate_middle(f"{header}\n{numbered}", MAX_OUTPUT_CHARS)
        return ToolResult(
            ok = True,
            output=output,
            metadata = {"path": path, "start": first, "end": first + len(lines) - 1,}
        )



