"""submit 工具：结束任务。"""
from __future__ import annotations

from typing import Any

from ..sandbox.base import Sandbox
from ..types import ToolResult


class SubmitTool:
    name = "submit"
    description = (
        "Call when the task is complete and the tests pass. This ends the run."
    )
    parameters = {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }

    def run(self, args: dict[str, Any], sandbox: Sandbox) -> ToolResult:
        return ToolResult(ok = True, output = "submitted")
