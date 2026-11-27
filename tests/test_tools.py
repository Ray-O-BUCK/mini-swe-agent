"""BashTool 和 ToolRegistry 的测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sweagent.sandbox.local import LocalSandbox
from sweagent.tools.base import ToolError, ToolRegistry, validate_arguments
from sweagent.tools.bash import BashTool
from sweagent.tools import default_registry
from sweagent.types import ExecResult

@pytest.fixture()
def sandbox(tmp_path: Path) -> LocalSandbox:
    return LocalSandbox(tmp_path)


@pytest.fixture()
def registry() -> ToolRegistry:
    return default_registry()


def test_schema_shape(registry: ToolRegistry) -> None:
    schemas = registry.schemas()
    names = {schema["function"]["name"] for schema in schemas}
    assert names == {
        "bash",
        "view",
        "str_replace",
        "create",
        "search",
        "submit",
    }
    assert all(schema["type"] == "function" for schema in schemas)


def test_bash_echo(registry: ToolRegistry, sandbox: LocalSandbox) -> None:
    result = registry.execute("bash", {"command": "echo hi"}, sandbox)
    assert result.ok
    assert "hi" in result.output


def test_bash_nonzero_exit(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    result = registry.execute(
        "bash",
        {"command": "python -c \"import sys; sys.exit(3)\""},
        sandbox,
    )
    assert not result.ok
    assert "exit_code: 3" in result.output


def test_bash_timeout(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    result = registry.execute(
        "bash",
        {
            "command": 'python -c "import time; time.sleep(5)"',
            "timeout": 1,
        },
        sandbox,
    )
    assert not result.ok
    assert "timed_out: true" in result.output


def test_bash_truncates_output(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    result = registry.execute(
        "bash",
        {"command": "python -c \"print('A' * 20000)\""},
        sandbox,
    )
    assert result.ok
    assert "中间输出已省略" in result.output
    assert len(result.output) <= 12_500


def test_registry_rejects_missing_argument(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    result = registry.execute("bash", {}, sandbox)
    assert not result.ok
    assert "缺少参数" in result.output


def test_registry_rejects_wrong_type(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    result = registry.execute("bash", {"command": 123}, sandbox)
    assert not result.ok
    assert "类型错误" in result.output


def test_registry_rejects_extra_argument(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    result = registry.execute(
        "bash",
        {"command": "echo hi", "foo": 1},
        sandbox,
    )
    assert not result.ok
    assert "不支持的参数" in result.output


def test_registry_rejects_unknown_tool(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    result = registry.execute("nope", {}, sandbox)
    assert not result.ok
    assert "未知工具" in result.output


def test_duplicate_tool_names_rejected() -> None:
    with pytest.raises(ValueError):
        ToolRegistry([BashTool(), BashTool()])


def test_validate_arguments_directly() -> None:
    tool = BashTool()
    with pytest.raises(ToolError):
        validate_arguments(tool, {})
    with pytest.raises(ToolError):
        validate_arguments(tool, {"command": 1})
    validate_arguments(tool, {"command": "echo hi"})


def test_view_line_numbers(
        registry: ToolRegistry,
        sandbox: LocalSandbox,
) -> None:
    sandbox.write_text("a.txt", "one\ntwo\nthree\n")
    result = registry.execute("view", {"path": "a.txt", "start": 2, "end": 3}, sandbox)
    assert result.ok
    assert "2 | two" in result.output
    assert "3 | three" in result.output


def test_view_caps_lines(
        registry: ToolRegistry,
        sandbox: LocalSandbox,
) -> None:
    sandbox.write_text(
        "a.txt",
        "\n".join(str(i) for i in range(300)) + "\n",
    )
    result = registry.execute("view", {"path": "a.txt"}, sandbox)
    assert result.ok
    assert "lines 1-200" in result.output
    assert "201 |" not in result.output


def test_str_replace_unique(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    sandbox.write_text("a.txt", "hello\nworld\n")
    result = registry.execute(
        "str_replace",
        {"path": "a.txt", "old": "world", "new": "there"},
        sandbox,
    )
    assert result.ok
    assert sandbox.read_text("a.txt") == "hello\nthere\n"


def test_str_replace_rejects_multiple(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    sandbox.write_text("a.txt", "x\nx\n")
    result = registry.execute(
        "str_replace",
        {"path": "a.txt", "old": "x", "new": "y"},
        sandbox,
    )
    assert not result.ok
    assert "2" in result.output

def test_str_replace_rejects_missing(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    sandbox.write_text("a.txt", "hello\n")
    result = registry.execute(
        "str_replace",
        {"path": "a.txt", "old": "nope", "new": "x"},
        sandbox,
    )
    assert not result.ok
    assert "not found" in result.output


def test_create_rejects_existing(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    sandbox.write_text("a.txt", "x")
    result = registry.execute(
        "create",
        {"path": "a.txt", "content": "y"},
        sandbox,
    )
    assert not result.ok
    assert "already exists" in result.output


def test_create_new_file(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    result = registry.execute(
        "create",
        {"path": "pkg/new.py", "content": "x = 1\n"},
        sandbox,
    )
    assert result.ok
    assert sandbox.read_text("pkg/new.py") == "x = 1\n"


def test_search_finds_match(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    sandbox.write_text("pkg/mod.py", "def target():\n    return 1\n")
    result = registry.execute("search", {"pattern": "target"}, sandbox)
    assert result.ok
    assert "pkg/mod.py" in result.output
    assert "target" in result.output


def test_search_no_matches(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    sandbox.write_text("a.txt", "hello\n")
    result = registry.execute("search", {"pattern": "nope"}, sandbox)
    assert result.ok
    assert "no matches" in result.output


def test_search_rejects_absolute_path(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    result = registry.execute(
        "search",
        {"pattern": "x", "path": "/etc"},
        sandbox,
    )
    assert not result.ok
    assert "relative" in result.output


def test_submit_tool(
    registry: ToolRegistry,
    sandbox: LocalSandbox,
) -> None:
    result = registry.execute("submit", {}, sandbox)
    assert result.ok
    assert result.output == "submitted"


def test_exec_result_output_format() -> None:
    result = ExecResult(
        command="x",
        exit_code=1,
        stdout="out",
        stderr="err",
        duration_ms=1,
    )
    assert result.output == (
        "exit_code: 1\nstdout:\nout\nstderr:\nerr"
    )