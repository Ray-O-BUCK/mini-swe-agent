"""上下文管理：按真实 prompt token 触发历史压缩。"""
from __future__ import annotations

from dataclasses import dataclass

from ..types import Message, Usage

# 估算用：英文/代码约 4 字符/token，中文约 1.5；取 3 偏保守（高估）。
_CHARS_PER_TOKEN = 3


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


def estimate_messages_tokens(messages: list[Message]) -> int:
    total = 0
    for message in messages:
        total += estimate_tokens(str(message.get('content') or ""))
        total += 4
        for call in message.get('tool_calls') or []:
            function = call.get('function') or {}
            total += estimate_tokens(str(function.get('name') or ""))
            total += estimate_tokens(str(function.get('arguments') or ""))
    return total


@dataclass(frozen=True)
class ContextConfig:
    max_prompt_tokens: int | None = None
    keep_recent_messages: int = 8
    summary_item_chars: int = 240

    def _post_init__(self) -> None:
        if self.max_prompt_tokens is not None and self.max_prompt_tokens < 1000:
            raise ValueError("max_prompt_tokens must be >= 1000")
        if self.keep_recent_messages < 2:
            raise ValueError("keep_recent_messages must be >= 2")
        if self.summary_item_chars < 40:
            raise ValueError("summary_item_chars must be >= 40")


@dataclass(frozen=True)
class CompactionInfo:
    before_messages: int
    after_messages: int
    before_tokens_est: int
    after_tokens_est: int
    last_prompt_tokens: int


class ContextManager:
    """跟踪真实 prompt token，超预算时把旧消息压成一条进度摘要。

    为什么用 last_prompt_tokens 而不是自己数 token：
    不同 provider 的 tokenizer 不同，本地估算必然有偏差；而 usage 里的
    prompt_tokens 是 provider 自己算的，触发条件是精确的。估算只用于
    日志里的前后对比。
    """

    def __init__(self, config: ContextConfig) -> None:
        self._config = config
        self._last_prompt_tokens = 0

    def observe_usage(self, usage: Usage) -> None:
        self._last_prompt_tokens = usage.prompt_tokens or 0

    def maybe_compact(self, messages: list[Message]) -> tuple[list[Message], CompactionInfo | None]:
        budget = self._config.max_prompt_tokens
        if budget is None or self._last_prompt_tokens <= budget:
            return messages, None
        if len(messages) <= 3:
            return messages, None

        keep = self._config.keep_recent_messages
        split = max(2, len(messages) - keep)
        # tool 消息 和 assistant 消息 不能拆开，所以从后往前数
        while split > 2 and messages[split].get('role') == 'tool':
            split -= 1

        old = messages[2:split]
        if not old:
            return messages, None

        # 压缩: 把 old 消息摘要成一条
        summary = _summarize(old, self._config.summary_item_chars)
        compacted = (messages[:2] + [{"role":"user","content":summary}] + messages[split:])
        return compacted, CompactionInfo(
            before_messages=len(messages),
            after_messages=len(compacted),
            before_tokens_est=estimate_messages_tokens(messages),
            after_tokens_est=estimate_messages_tokens(compacted),
            last_prompt_tokens=self._last_prompt_tokens,
        )


def _summarize(messages: list[Message], item_chars: int) -> str:
    lines = ["Earlier progress (compacted to save context):"]
    for message in messages:
        role = message.get("role")
        content = (message.get("content") or "").strip().replace("\n", " ")
        if role == "assistant":
            calls = message.get("tool_calls") or []
            if calls:
                described = ", ".join(
                    f"{c.get('function', {}).get('name')}"
                    f"({_clip(str(c.get('function', {}).get('arguments') or ''), 80)})"
                    for c in calls
                )
                lines.append(
                    f"- assistant: {_clip(content, item_chars)} | calls: {described}"
                )
            elif content:
                lines.append(f"- assistant: {_clip(content, item_chars)}")
        elif role == "tool":
            lines.append(f"- result: {_clip(content, item_chars)}")
        elif role == "user":
            lines.append(f"- observation: {_clip(content, item_chars)}")
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."
