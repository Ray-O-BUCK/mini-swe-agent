"""工具实现与注册表。"""
from __future__ import annotations

from .base import ToolRegistry
from .bash import BashTool
from .edit import CreateTool, StrReplaceTool
from .search import SearchTool
from .submit import SubmitTool
from .view import ViewTool


def default_registry() -> ToolRegistry:
    """创建包含全部 S4 工具的注册表。"""
    return ToolRegistry(
        [
            BashTool(),
            ViewTool(),
            StrReplaceTool(),
            CreateTool(),
            SearchTool(),
            SubmitTool(),
        ]
    )