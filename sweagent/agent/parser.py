"""解析 bash-only 文本动作和原生 tool calls。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from ..types import ToolCall

_BASH_BLOCK_RE = re.compile(
    r"```(?:bash|sh|shell)\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
# DeepSeek 在没给 tools 时仍可能输出 DSML 工具调用文本，例如：
#   <｜｜DSML｜｜ invoke name="bash">
#   <｜｜DSML｜｜ parameter name="command" string="true">ls -la</｜｜DSML｜｜ parameter>
#   </｜｜DSML｜｜ invoke>
# 分隔符是全角竖线 U+FF5C，且不同版本可能变化，所以只匹配语义关键字。
_DSML_BASH_RE = re.compile(
    r'invoke\s+name="bash"\s*>.*?'
    r'parameter\s+name="command"[^>]*>\s*(.*?)\s*</[^>]*parameter>',
    re.DOTALL,
)
# 有时模型会省略 invoke 标签，只留 parameter：
#   <｜｜DSML｜｜ parameter name="command">ls -la</...parameter>
# 只要出现 command 参数就当成 bash 动作；工具里只有 bash 有 command 参数。
_DSML_COMMAND_RE = re.compile(
    r'parameter\s+name="command"[^>]*>\s*(.*?)\s*</[^>]*parameter>',
    re.DOTALL,
)
# 有时模型只写开头围栏，结尾被 DSML 结束标签替换：
#   ```bash\nls -la</｜｜DSML｜｜ parameter>\n</...invoke>
_UNCLOSED_BASH_RE = re.compile(
    r"```(?:bash|sh|shell)\s*\n(.*)$",
    re.DOTALL | re.IGNORECASE,
)
# 或者 command 参数没有闭合标签。
_DSML_COMMAND_OPEN_RE = re.compile(
    r'parameter\s+name="command"[^>]*>\s*(.*)$',
    re.DOTALL,
)
# 匹配 DSML 结束标签，用于从命令尾部切掉标记。
_MARKUP_TAIL_RE = re.compile(
    r"</[^>\n]*(?:parameter|invoke|calls)[^>]*>",
    re.IGNORECASE,
)
_SUBMIT_RE = re.compile(r"(?m)^\s*SUBMIT\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class Action:
    """统一的动作表示。

    kind="tool" 时，name/arguments/tool_call_id 有意义；
    kind="submit" 时，loop 直接结束；
    kind="none" 表示没有解析出动作。
    """

    kind: Literal["tool", "submit", "none"]
    name: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str | None = None
    notes: tuple[str, ...] = ()


def parse_bash_action(content: str) -> Action:
    """从 bash-only 回复里提取一个动作。

    优先级：bash 代码块 > 单独的 SUBMIT > 无动作。
    先看代码块，避免 `echo SUBMIT` 这种块内文本被误判为结束。
    """
    blocks = _BASH_BLOCK_RE.findall(content)
    if blocks:
        command = blocks[0].strip()
        notes: list[str] = []
        if len(blocks) > 1:
            notes.append(
                "Only the first bash block was executed; use one action per turn."
            )
        if not command:
            return Action(kind="none", notes=("The bash block was empty.",))
        return Action(
            kind="tool",
            name="bash",
            arguments={"command": command},
            notes=tuple(notes),
        )

    # 兜底：模型有时用 DSML 工具调用文本而不是代码块。
    # 不解析它，这些步就变成空动作，会不公平地拉低 bash-only 基线。
    dsml = _DSML_BASH_RE.search(content) or _DSML_COMMAND_RE.search(content)
    if dsml:
        command = dsml.group(1).strip()
        if command:
            return Action(
                kind="tool",
                name="bash",
                arguments={"command": command},
                notes=("Parsed DSML-style tool call as bash.",),
            )

    # 兜底 2：未闭合的 bash 围栏，或未闭合的 command 参数。
    for pattern in (_UNCLOSED_BASH_RE, _DSML_COMMAND_OPEN_RE):
        broken = pattern.search(content)
        if broken:
            command = _clean_unclosed(broken.group(1))
            if command:
                return Action(
                    kind="tool",
                    name="bash",
                    arguments={"command": command},
                    notes=("Parsed unclosed bash block.",),
                )

    if _SUBMIT_RE.search(content):
        return Action(kind="submit")

    return Action(kind="none")


def _clean_unclosed(text: str) -> str:
    """清掉未闭合命令尾部的 DSML 标记和围栏。"""
    match = _MARKUP_TAIL_RE.search(text)
    if match:
        text = text[: match.start()]
    lines = text.splitlines()
    while lines:
        stripped = lines[-1].strip()
        if stripped in ("", "```"):
            lines.pop()
            continue
        break
    return "\n".join(lines).strip()


def actions_from_tool_calls(tool_calls: list[ToolCall]) -> list[Action]:
    """把 LLM 返回的原生 tool calls 转成统一的 Action。"""
    return [
        Action(
            kind="tool",
            name=call.name,
            arguments=call.arguments,
            tool_call_id=call.id,
        )
        for call in tool_calls
    ]
