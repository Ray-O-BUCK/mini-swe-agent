"""Parser 的单元测试。"""
from __future__ import annotations

from sweagent.agent.parser import actions_from_tool_calls, parse_bash_action
from sweagent.types import ToolCall


def test_parse_bash_block() -> None:
    action = parse_bash_action("think\n```bash\nls -la\n```")
    assert action.kind == "tool"
    assert action.name == "bash"
    assert action.arguments == {"command": "ls -la"}


def test_parse_submit_text() -> None:
    action = parse_bash_action("done\nSUBMIT\n")
    assert action.kind == "submit"


def test_echo_submit_inside_block_is_not_submit() -> None:
    action = parse_bash_action("```bash\necho SUBMIT\n```")
    assert action.kind == "tool"
    assert action.name == "bash"


def test_multiple_blocks_use_first_and_note() -> None:
    action = parse_bash_action(
        "```bash\nls\n```\n```bash\npwd\n```"
    )
    assert action.kind == "tool"
    assert action.arguments == {"command": "ls"}
    assert action.notes


def test_parse_dsml_style_tool_call() -> None:
    # DeepSeek 在未提供 tools 时输出过这种全角竖线分隔的 DSML 文本。
    content = (
        "\n\n<｜｜DSML｜｜ calls>\n"
        '<｜｜DSML｜｜ invoke name="bash">\n'
        '<｜｜DSML｜｜ parameter name="command" '
        'string="true">sed -n 200,320p src/a.py</｜｜DSML｜｜ parameter>\n'
        "</｜｜DSML｜｜ invoke>\n"
        "</｜｜DSML｜｜ calls>"
    )
    action = parse_bash_action(content)
    assert action.kind == "tool"
    assert action.name == "bash"
    assert action.arguments == {
        "command": "sed -n 200,320p src/a.py"
    }


def test_parse_dsml_without_invoke_name() -> None:
    # 实测变体：没有 invoke name，直接 parameter。
    content = (
        "<｜｜DSML｜｜ calls>\n"
        '<｜｜DSML｜｜ parameter name="command">'
        "sed -n 560,620p src/a.py</｜｜DSML｜｜ parameter>\n"
        "</｜｜DSML｜｜ calls>"
    )
    action = parse_bash_action(content)
    assert action.kind == "tool"
    assert action.arguments == {"command": "sed -n 560,620p src/a.py"}


def test_parse_dsml_pipe_variant() -> None:
    # 实测变体：invoke 和 parameter 之间用 |
    content = (
        "<｜｜DSML｜｜ calls>|<parameter name=\"command\">"
        'grep -rn "envvar" tests</parameter>\n</invoke>'
    )
    action = parse_bash_action(content)
    assert action.kind == "tool"
    assert action.arguments == {"command": 'grep -rn "envvar" tests'}


def test_parse_unclosed_bash_fence_with_dsml_tail() -> None:
    content = (
        "\n\n```bash\nls -la && git status"
        "</｜｜DSML｜｜ parameter>\n"
        "</｜｜DSML｜｜ invoke>\n"
        "</｜｜DSML｜｜ calls>"
    )
    action = parse_bash_action(content)
    assert action.kind == "tool"
    assert action.arguments == {"command": "ls -la && git status"}


def test_parse_unclosed_command_parameter() -> None:
    content = (
        '<｜｜DSML｜｜ invoke name="bash">\n'
        '<｜｜DSML｜｜ parameter name="command" string="true">'
        "sed -n 380,430p src/a.py\n```"
    )
    action = parse_bash_action(content)
    assert action.kind == "tool"
    assert action.arguments == {
        "command": "sed -n 380,430p src/a.py"
    }


def test_actions_from_tool_calls() -> None:
    calls = [
        ToolCall(
            id="call_1",
            name="view",
            arguments={"path": "calc.py"},
            raw_arguments='{"path": "calc.py"}',
        )
    ]
    actions = actions_from_tool_calls(calls)
    assert len(actions) == 1
    assert actions[0].kind == "tool"
    assert actions[0].name == "view"
    assert actions[0].arguments == {"path": "calc.py"}
    assert actions[0].tool_call_id == "call_1"
