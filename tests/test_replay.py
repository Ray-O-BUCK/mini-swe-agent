"""replay 子命令的简单测试。"""
from __future__ import annotations

import argparse
from pathlib import Path

from sweagent.cli import _cmd_replay

def test_replay_prints_events(
    tmp_path: Path,
    capsys,
) -> None:
    path = tmp_path / "trajectory.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"type":"run_start","mode":"tools","max_steps":2,'
                '"prompt_version":"x","task":"t"}',
                '{"type":"llm","step":1,"model":"fake","content":"hello",'
                '"tool_calls":[],"finish_reason":"stop","latency_ms":1,'
                '"usage":{}}',
                '{"type":"action","step":1,"name":"bash",'
                '"arguments":{"command":"echo hi"}}',
                '{"type":"observation","step":1,"name":"bash","ok":true,'
                '"output":"hi"}',
                '{"type":"run_end","status":"submitted","steps":1,'
                '"error":null}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    args = argparse.Namespace(trajectory=str(path), max_chars=100)
    assert _cmd_replay(args) == 0
    output = capsys.readouterr().out
    assert "run_start" in output
    assert "LLM" in output
    assert "run_end" in output