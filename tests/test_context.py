"""ContextManager 的单元测试。"""
from __future__ import annotations

from sweagent.agent.context import (
    ContextConfig,
    ContextManager,
    estimate_messages_tokens,
    estimate_tokens,
)
from sweagent.types import Usage


def _messages_with_tool_pairs(pairs: int) -> list[dict]:
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
    ]
    for index in range(pairs):
        call_id = f"call_{index}"
        messages.append(
            {
                "role": "assistant",
                "content": f"thinking {index}",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command": "echo hi"}',
                        },
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": "x" * 2000,
            }
        )
    return messages


def test_estimate_tokens_grows_with_length() -> None:
    assert estimate_tokens("") == 1
    assert estimate_tokens("x" * 300) > estimate_tokens("x" * 30)


def test_no_compaction_below_budget() -> None:
    manager = ContextManager(ContextConfig(max_prompt_tokens=10_000))
    messages = _messages_with_tool_pairs(3)
    manager.observe_usage(Usage(prompt_tokens=5_000))
    compacted, info = manager.maybe_compact(messages)
    assert compacted is messages
    assert info is None


def test_no_compaction_without_budget() -> None:
    manager = ContextManager(ContextConfig())
    messages = _messages_with_tool_pairs(3)
    manager.observe_usage(Usage(prompt_tokens=999_999))
    compacted, info = manager.maybe_compact(messages)
    assert compacted is messages
    assert info is None


def test_compaction_keeps_head_and_tail() -> None:
    manager = ContextManager(
        ContextConfig(max_prompt_tokens=1_000, keep_recent_messages=4)
    )
    messages = _messages_with_tool_pairs(10)
    manager.observe_usage(Usage(prompt_tokens=5_000))
    compacted, info = manager.maybe_compact(messages)

    assert info is not None
    assert compacted[0] == messages[0]
    assert compacted[1] == messages[1]
    assert compacted[2]["role"] == "user"
    assert "compacted" in compacted[2]["content"]
    assert compacted[-1] == messages[-1]
    assert len(compacted) < len(messages)


def test_compaction_does_not_orphan_tool_messages() -> None:
    manager = ContextManager(
        ContextConfig(max_prompt_tokens=1_000, keep_recent_messages=4)
    )
    messages = _messages_with_tool_pairs(10)
    manager.observe_usage(Usage(prompt_tokens=5_000))
    compacted, _ = manager.maybe_compact(messages)

    # tail 不能以 tool 开头：每个 tool 消息前面的 assistant 必须还在。
    tail = compacted[3:]
    assert tail[0]["role"] != "tool"
    open_calls: set[str] = set()
    for message in tail:
        if message["role"] == "assistant":
            for call in message.get("tool_calls") or []:
                open_calls.add(call["id"])
        elif message["role"] == "tool":
            assert message["tool_call_id"] in open_calls


def test_compaction_reduces_estimated_tokens() -> None:
    manager = ContextManager(
        ContextConfig(max_prompt_tokens=1_000, keep_recent_messages=4)
    )
    messages = _messages_with_tool_pairs(10)
    manager.observe_usage(Usage(prompt_tokens=5_000))
    compacted, info = manager.maybe_compact(messages)
    assert info is not None
    assert info.after_tokens_est < info.before_tokens_est
    assert estimate_messages_tokens(compacted) < estimate_messages_tokens(messages)
