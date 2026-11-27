"""OpenAI 兼容的 LLM 客户端。

为什么要这层封装：
- 其余代码不应该依赖 OpenAI SDK 的响应对象；
- 每次调用都返回同一种 LLMResponse（content、tool_calls、usage、latency、
  finish_reason），loop 因此与 provider 无关，也容易在测试里 fake；
- 重试只留在传输层；agent 级别的恢复逻辑以后由 loop 负责。
"""
from __future__ import annotations

import json
import time
from typing import Any

from openai import OpenAI

from .config import LLMConfig
from .types import LLMResponse, Message, ToolCall, Usage

class LLMError(RuntimeError):
    """请求失败（网络、鉴权、限流、provider 错误）。"""

class LLMProtocolError(LLMError):
    """provider 返回了我们无法安全解释的内容。"""


class LLMClient:
    def __init__(self, config: LLMConfig) -> None:
        self._config = config
        self._client = OpenAI(
            api_key = config.api_key,
            base_url = config.base_url,
            timeout = config.timeout_s,
            max_retries = config.max_retries,
        )

    def chat(
            self,
            messages: list[Message],
            *,
            tools: list[dict[str, Any]] | None = None,
            temperature: float | None = None,
            max_tokens: int | None = None,
             ) -> LLMResponse:
        request: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "temperature":(self._config.temperature if temperature is None else temperature),
            "max_tokens":(self._config.max_tokens if max_tokens is None else max_tokens),
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"

        started = time.perf_counter()
        try:
            response = self._client.chat.completions.create(**request)
        except Exception as exc:
            # 不要把请求头回显出来；API key 绝不能进日志。
            raise LLMError(f"LLM 请求失败: {type(exc).__name__}: {exc}") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if not response.choices:
            raise LLMProtocolError("provider 没有返回任何 choice。")

        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        for index, call in enumerate(message.tool_calls or []):
            function = getattr(call, "function", None)
            if function is None or not getattr(function, "name", None):
                raise LLMProtocolError(
                    "provider 返回了一个没有函数名的 tool call。"
                )
            raw = function.arguments or "{}"
            try:
                arguments = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise LLMProtocolError(
                    f"工具 {function.name!r} 的参数不是合法 JSON：{raw!r}"
                ) from exc
            if not isinstance(arguments, dict):
                raise LLMProtocolError(
                    f"工具 {function.name!r} 的参数必须是 JSON 对象，"
                    f"实际是 {type(arguments).__name__}。"
                )
            tool_calls.append(
                ToolCall(
                    id = call.id or f"call_{index}",
                    name = function.name,
                    arguments = arguments,
                    raw_arguments = raw,
                )
            )

        usage = Usage()
        if response.usage is not None:
            usage = Usage(
                prompt_tokens = response.usage.prompt_tokens or 0,
                completion_tokens = response.usage.completion_tokens or 0,
                total_tokens = response.usage.total_tokens or 0,
            )

        return LLMResponse(
            content = message.content,
            tool_calls = tool_calls,
            usage = usage,
            model = getattr(response, "model", None) or self._config.model,
            finish_reason = choice.finish_reason,
            latency_ms = latency_ms,
        )
