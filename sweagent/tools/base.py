"""Tool Protocol、参数校验和注册表。

工具只负责“把一次动作翻译成 sandbox 操作”，不决定下一步做什么。所有预期
失败（缺参数、类型错、路径越界、命令超时）都应该变成 ToolResult，而不是让
agent loop 崩掉。
"""
from __future__ import annotations

from typing import Any, Iterable, Protocol

from ..sandbox.base import Sandbox, SandboxError
from ..types import ToolResult


class ToolError(RuntimeError):
    """工具的可预期错误；registry 会把它转成 ok=False 的 ToolResult。"""


class Tool(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]

    def run(self, args: dict[str, Any], sandbox: Sandbox) -> ToolResult:
        ...


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool]) -> None:
        tool_list = list(tools)
        names = [tool.name for tool in tool_list]
        if len(names) != len(set(names)):
            raise ValueError(f"工具名重复：{names}")
        self._tools = {tool.name: tool for tool in tool_list}

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolError(f"未知工具：{name!r}") from exc

    def schemas(self) -> list[dict[str, Any]]:
        """转成 OpenAI function calling 需要的 tools 列表。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def execute(
        self,
        name: str,
        args: dict[str, Any],
        sandbox: Sandbox,
    ) -> ToolResult:
        try:
            tool = self.get(name)
            validate_arguments(tool, args)
            return tool.run(args, sandbox)
        except ToolError as exc:
            return ToolResult(ok=False, output=f"工具错误：{exc}")
        except SandboxError as exc:
            return ToolResult(ok=False, output=f"沙箱错误：{exc}")
        except Exception as exc:
            # 生产代码这里应该 logger.exception；现在先把错误喂回模型，
            # 同时保留类型和消息，方便从 trajectory 里定位 bug。
            return ToolResult(
                ok=False,
                output=f"工具 {name} 内部错误：{type(exc).__name__}: {exc}",
            )


def validate_arguments(tool: Tool, args: dict[str, Any]) -> None:
    """按工具自己的 parameters schema 做最小校验。

    不做完整 JSON Schema；只检查 required、基础类型、integer 的 min/max，
    以及 additionalProperties=False 时的多余字段。够挡住模型的常见错误。
    """
    if not isinstance(args, dict):
        raise ToolError(f"参数必须是 JSON 对象，实际是 {type(args).__name__}")

    required = tool.parameters.get("required", [])
    for key in required:
        if key not in args:
            raise ToolError(f"缺少参数：{key!r}")

    properties = tool.parameters.get("properties", {})
    if tool.parameters.get("additionalProperties") is False:
        extra = sorted(set(args) - set(properties))
        if extra:
            raise ToolError(f"不支持的参数：{extra}")

    for key, value in args.items():
        spec = properties.get(key)
        if not spec:
            continue
        expected = spec.get("type")
        if expected and not _matches_type(value, expected):
            raise ToolError(
                f"参数 {key!r} 类型错误：期望 {expected}，"
                f"实际是 {type(value).__name__}"
            )
        if expected == "integer":
            minimum = spec.get("minimum")
            maximum = spec.get("maximum")
            if minimum is not None and value < minimum:
                raise ToolError(f"参数 {key!r} 必须 >= {minimum}")
            if maximum is not None and value > maximum:
                raise ToolError(f"参数 {key!r} 必须 <= {maximum}")


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    # 未知类型不拦，交给具体工具处理。
    return True
