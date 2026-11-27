"""编辑工具：str_replace 和 create。"""
from __future__ import annotations

from typing import Any

from ..sandbox.base import Sandbox
from ..types import ToolResult
from .base import ToolError


class StrReplaceTool:
    name = "str_replace"
    description = (
        "Replace an exact string in a text file. The old string must occur "
        "exactly once; otherwise the edit is rejected. Use enough surrounding "
        "context to make it unique."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the repository root.",
            },
            "old": {
                "type": "string",
                "description": "Exact text to replace. Must be non-empty and unique.",
            },
            "new": {
                "type": "string",
                "description": "Replacement text. May be empty to delete.",
            },
        },
        "required": ["path", "old", "new"],
        "additionalProperties": False,
    }

    def run(self, args: dict[str, Any], sandbox: Sandbox) -> ToolResult:
        path = args['path']
        old = args['old']
        new = args['new']
        if not old:
            raise ToolError("old text must be non-empty")

        content = sandbox.read_text(path)
        count = content.count(old)
        if count == 0:
            raise ToolError(f"old text not found in {path!r}; view the file and copy the exact text without line-number prefixes")
        if count > 1:
            raise ToolError(
                f"old text appears {count} times in {path!r}; include more "
                "surrounding context to make it unique"
            )

        updated = content.replace(old, new, 1)
        if updated == content:
            return ToolResult(ok = True, output = f"no change in {path}")
        sandbox.write_text(path, updated)
        return ToolResult(ok = True, output = f"edited {path}: replaced 1 occurrence")

class CreateTool:
    name = "create"
    description = (
        "Create a new text file. Fails if the file already exists; use str_replace to edit an existing file."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to the repository root.",
            },
            "content": {
                "type": "string",
                "description": "Full file content.",
            },
        },
        "required": ["path", "content"],
        "additionalProperties": False,
    }

    def run(self, args: dict[str, Any], sandbox: Sandbox) -> ToolResult:
        path = args['path']
        content = args['content']
        if sandbox.exists(path):
            raise ToolError(
                f"{path!r} already exists; use str_replace to edit it"
            )
        sandbox.write_text(path, content)
        return ToolResult(
            ok = True,
            output=f"created {path} ({len(content)} chars)"
        )
