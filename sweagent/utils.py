"""通用小工具。"""
from __future__ import annotations


def truncate_middle(text: str, max_chars: int) -> str:
    """保留开头和结尾，中间用标记代替。

    为什么不直接截断开头：命令输出和测试日志的关键信息经常在末尾
    （失败摘要、traceback 的最后一行），只留开头会丢掉最重要的部分。
    """
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text

    marker = "\n... [中间输出已省略] ...\n"
    if len(marker) >= max_chars:
        return marker[:max_chars]

    budget = max_chars - len(marker)
    head_len = budget * 2 // 3
    tail_len = budget - head_len
    tail = text[-tail_len:] if tail_len > 0 else ""
    return text[:head_len] + marker + tail
