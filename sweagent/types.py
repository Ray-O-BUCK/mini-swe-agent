"""共享数据类型。

把 provider 特有的对象挡在其余代码之外。agent loop、工具和 benchmark
都应该只依赖这些小而可测试的类型。
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, TypeAlias

# OpenAI 兼容接口里的 chat message。这里先用 dict，方便 tool 消息携带
# tool_call_id、name 等额外字段；之后如果值得，再升级成 TypedDict。
Message: TypeAlias = dict[str, Any]


@dataclass(frozen=True)
class Usage:
    """一次或多次 LLM 调用的 token 统计。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass(frozen=True)
class ToolCall:
    """归一化后的工具调用。

    arguments 是解析后的 JSON；raw_arguments 保留原始字符串，方便日志记录，
    也方便后面把坏 JSON 作为 observation 喂回模型。
    """

    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str


@dataclass(frozen=True)
class LLMResponse:
    """agent loop 从一次模型调用里真正需要的字段。"""

    content: str | None
    tool_calls: list[ToolCall]
    usage: Usage
    model: str
    finish_reason: str | None
    latency_ms: int

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass(frozen=True)
class ExecResult:
    """一次命令执行的结果。

    timed_out=True 时 exit_code 固定为 -1，stdout/stderr 可能包含超时前的
    部分输出。是否算成功由具体工具决定，sandbox 不做语义判断。
    """

    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    @property
    def output(self) -> str:
        """完整 stdout/stderr 的展示文本。

        sandbox 只负责执行，不负责截断；工具/评测层可以按自己的预算
        对这个结果做 truncate。格式统一在这里，避免两套拼接逻辑。
        """
        parts = [f"exit_code: {self.exit_code}"]
        if self.timed_out:
            parts.append("timed_out: true")
        if self.stdout:
            parts.append(f"stdout:\n{self.stdout}")
        if self.stderr:
            parts.append(f"stderr:\n{self.stderr}")
        if not self.stdout and not self.stderr:
            parts.append("(无输出)")
        return "\n".join(parts)


@dataclass(frozen=True)
class ToolResult:
    """工具返回给 agent loop 的 observation。

    ok=False 不代表程序崩溃，而是“这次动作失败了，把 output 喂回模型让它
    自己修正”。真正的 bug 才应该抛异常。
    """

    ok: bool
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunResult:
    """一次 agent run 的结果。

        status:
            - submitted: 模型主动 SUBMIT
            - max_steps: 用完步数预算
            - looping: 同一动作重复达到上限
            - error: LLM/内部异常
            - repair_failed: 可见测试多次未通过，拒绝提交后放弃
        """

    status: str
    steps: int
    patch: str
    usage: Usage
    trajectory_path: Path
    error: str | None = None
