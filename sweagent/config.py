"""应用配置。

规则：
- 密钥只从环境变量读取，绝不写进代码；
- 加载时尽早失败，并给出清楚的错误信息；
- 配置对象不可变，且永远不会打印 API key。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# 从当前文件反推项目根目录，而不是依赖当前工作目录。
# 这样 `python -m sweagent.cli` 在任何目录下都能找到 .env。
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

class ConfigError(RuntimeError):
    """配置缺失或非法时抛出。"""

def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(
            f"缺少环境变量 {name}。请把.env.example 复制为 .env 并填写。"
        )
    return value

def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是数字，当前值是 {raw!r}") from exc

def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数，当前值是{raw!r}") from exc


@dataclass(frozen=True)
class LLMConfig:
    # repr=False 可以避免 key 出现在日志、打印 config 的 traceback，以及误写`print(config.llm)`的输出里。
    api_key: str= field(repr=False)
    base_url: str
    model: str
    timeout_s: float = 60.0
    max_retries: int = 8
    temperature: float = 0.0
    max_tokens: int = 8192


@dataclass(frozen=True)
class AppConfig:
    llm: LLMConfig
    project_root: Path = PROJECT_ROOT


def load_config() -> AppConfig:
    api_key = _require_env("LLM_API_KEY")
    if api_key == "your_api_key_here":
        raise ConfigError(
            "LLM_API_KEY 还是占位符。请把真实 key 写进 .env"
            "（并确保 .env 不进 git）。"
        )

    temperature = _env_float("LLM_TEMPERATURE", 0.0)
    if not 0.0 <= temperature <= 2.0:
        raise ConfigError(f"LLM_TEMPERATURE 必须在 0.0 到 2.0 之间, 当前值是 {temperature}")

    max_tokens = _env_int("LLM_MAX_TOKENS", 8192)
    if max_tokens <= 0:
        raise ConfigError(f"LLM_MAX_TOKENS 必须为正数, 当前值是 {max_tokens}")

    timeout_s = _env_float("LLM_TIMEOUT", 60.0)
    if timeout_s <= 0:
        raise ConfigError(f"LLM_TIMEOUT 必须为正数, 当前值是 {timeout_s}")

    max_retries = _env_int("LLM_MAX_RETRIES", 3)
    if max_retries < 0:
        raise ConfigError(f"LLM_MAX_RETRIES 必须 >=0, 当前值是 {max_retries}")

    return AppConfig(
        llm=LLMConfig(
            api_key=api_key,
            base_url=_require_env("LLM_BASE_URL"),
            model=_require_env("LLM_MODEL"),
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
            max_retries=max_retries,
        ),
    )


