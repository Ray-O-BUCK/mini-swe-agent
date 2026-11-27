"""LocalSandbox 的边界测试。

重点不是 happy path，而是失败路径：路径越界、二进制文件、超时、密钥泄漏。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sweagent.sandbox.docker import build_container_args
from sweagent.sandbox.base import SandboxError
from sweagent.sandbox.local import LocalSandbox


def test_docker_container_args_are_hardened(tmp_path: Path) -> None:
    args = build_container_args("sweagent-test", "img:tag", tmp_path)
    joined = " ".join(args)
    expected = [
        "--network none",
        "--memory 1g",
        "--memory-swap 1g",
        "--cpus 1",
        "--pids-limit 128",
        "--cap-drop ALL",
        "--security-opt no-new-privileges",
        "--read-only",
        "--tmpfs /tmp:rw,size=256m",
        "--user 1000:1000",
    ]
    for flag in expected:
        assert flag in joined
    assert f"{tmp_path}:/workspace" in joined


def test_write_and_read_roundtrip(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    sandbox.write_file("a/b.txt", "hello\nworld\n")
    assert sandbox.read_file("a/b.txt") == "hello\nworld"


def test_read_line_range(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    sandbox.write_file("a.txt", "one\ntwo\nthree\n")
    assert sandbox.read_file("a.txt", 2, 3) == "two\nthree"
    assert sandbox.read_file("a.txt", 2) == "two\nthree"


def test_path_traversal_rejected(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    outside = tmp_path.parent / "outside.txt"
    with pytest.raises(SandboxError):
        sandbox.write_file("../outside.txt", "x")
    with pytest.raises(SandboxError):
        sandbox.read_file("../outside.txt")
    assert not outside.exists()


def test_absolute_path_rejected(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    with pytest.raises(SandboxError):
        sandbox.read_file("/etc/passwd")


def test_binary_file_rejected(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02")
    with pytest.raises(SandboxError):
        sandbox.read_file("bin.dat")


def test_missing_file_rejected(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    with pytest.raises(SandboxError):
        sandbox.read_file("nope.txt")


def test_exec_echo(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    result = sandbox.exec("echo hi")
    assert result.exit_code == 0
    assert "hi" in result.stdout
    assert not result.timed_out


def test_exec_timeout(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    result = sandbox.exec(
        'python -c "import time; time.sleep(5)"',
        timeout=1,
    )
    assert result.timed_out
    assert result.exit_code == -1


def test_env_does_not_leak_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_API_KEY", "secret-value")
    sandbox = LocalSandbox(tmp_path)
    result = sandbox.exec(
        "python -c \"import os; print(os.getenv('LLM_API_KEY'))\""
    )
    assert result.exit_code == 0
    assert "secret-value" not in result.stdout


def test_read_write_text_preserves_trailing_newline(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    sandbox.write_text("a.txt", "hello\n")
    assert sandbox.read_text("a.txt") == "hello\n"

    sandbox.write_text("a.txt", "world")
    assert sandbox.read_text("a.txt") == "world"


def test_exists(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    assert not sandbox.exists("a.txt")
    sandbox.write_text("a.txt", "x")
    assert sandbox.exists("a.txt")
    assert not sandbox.exists(".")


def test_pipeline_exit_code(tmp_path: Path) -> None:
    sandbox = LocalSandbox(tmp_path)
    result = sandbox.exec(
        'python -c "import sys; sys.exit(3)" '
        '| python -c "import sys; sys.exit(0)"'
    )
    assert result.exit_code == 3

