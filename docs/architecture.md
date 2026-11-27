# 架构与设计

## 总体分层

```text
CLI (cli.py)
  ├── run   : AgentLoop + Sandbox + ToolRegistry
  ├── eval  : TaskSpec + evaluate_patch
  ├── bench : load_tasks + run_benchmark + report
  ├── replay: trajectory.jsonl → 可读文本
  └── smoke : LLMClient 连通性

AgentLoop
  ├── LLMClient        OpenAI 兼容调用，归一化 usage/tool_calls
  ├── Parser           bash 代码块 / DSML / tool calls → Action
  ├── ContextManager   按真实 prompt tokens 压缩历史（默认关闭）
  ├── ToolRegistry     按名字找工具、校验参数、执行
  ├── Sandbox          exec / read_text / write_text
  └── TrajectoryWriter JSONL 事件流

Benchmark
  ├── TaskSpec         base/fix commit、issue、f2p/p2p、hidden patch
  ├── runner           每任务：fresh workspace → agent → patch → eval
  ├── evaluate         apply patch → reset tests → hidden patch → pytest
  └── report           report.md / summary.json
```

## 关键 Protocol

### Sandbox

```python
class Sandbox(Protocol):
    def exec(self, command: str, timeout: int = 60) -> ExecResult: ...
    def read_text(self, path: str) -> str: ...
    def write_text(self, path: str, content: str) -> None: ...
    def exists(self, path: str) -> bool: ...
    def read_file(self, path, start=None, end=None) -> str: ...
    def write_file(self, path, content: str) -> None: ...
    def close(self) -> None: ...
```

- `LocalSandbox`：临时目录 + subprocess + 路径守卫 + env 白名单；**不是安全边界**。
- `DockerSandbox`：bind mount + `docker exec`，文件仍走宿主 Path；容器参数见 `docs/sandbox.md`。

### Tool

```python
class Tool(Protocol):
    name: str
    description: str
    parameters: dict
    def run(self, args: dict, sandbox: Sandbox) -> ToolResult: ...
```

`ToolRegistry.schemas()` 直接把 `name/description/parameters` 转成 OpenAI function calling 的 tools，同一份 schema 也用于参数校验。

## AgentLoop 的状态机

```text
messages = [system, task]
for step in 1..max_steps:
    [truncated response?] → 喂回提示，continue
    response = llm.chat(messages, tools?)
    observe usage (context manager)
    actions = parse(response)
    for action in actions:
        submit? → 可选可见测试闸门 → 结束/拒绝
        repeated? → 警告/looping
        execute tool → observation
    append observation
```

终止状态：

| status | 含义 |
| :--- | :--- |
| `submitted` | 模型提交，且闸门通过（如启用） |
| `max_steps` | 用完预算 |
| `looping` | 连续相同动作达到上限 |
| `repair_failed` | 可见测试多次未通过 |
| `error` | LLM/内部异常 |

## 消息协议

### bash 模式

```text
assistant: 思考 + ```bash 代码块（或 DSML 文本）
user:      Observation (step i/N): exit_code/stdout/stderr
```

parser 依次尝试：

1. 闭合的 ```bash / ```sh / ```shell 代码块；
2. `invoke name="bash"` + `parameter name="command"` 的 DSML；
3. 省略 invoke 名、只有 command 参数的 DSML；
4. 未闭合的 bash 围栏（尾部 DSML 标记会被切掉）；
5. 单独的 `SUBMIT`。

### tools 模式

```text
assistant: { role, content, tool_calls: [{id, type, function:{name, arguments}}] }
tool:      { role: "tool", tool_call_id, content }
```

tool 消息必须与 `tool_call_id` 配对；压缩历史时 tail 不能以 tool 消息开头。

## 评测流水线

```text
1. 从 base_commit 生成 fresh workspace（git archive + git init + 单 commit）
2. agent 在沙箱里运行，产出 patch
3. 评测重新生成 fresh workspace
4. 应用 model patch
5. 把 task.test_files 重置到 baseline
6. 注入 hidden test patch
7. 跑 FAIL_TO_PASS + PASS_TO_PASS
8. resolved = exit_code == 0
```

顺序不能换：先应用 model patch 才能保留修复；再重置测试文件才能丢弃模型对测试的修改；最后注入 hidden test 才是裁判。

## 防作弊不变量

- workspace 没有 fix commit、没有 hidden test；
- `git log` 只有 baseline；`git fsck` 找不到未来对象；
- 评测用新容器，不复用 agent 容器；
- hidden patch 用 `docker cp`/workspace 内文件注入，不挂载 `tasks/`；
- `--network none`，无法访问上游仓库；
- 失败时 `finally` 清理容器；异常中断需手动清理。

## 可复现性的代价

- `VectorRetriever` 式的精确检索思路同样适用于 workspace：用确定的 `git archive`，不依赖工作树状态；
- 文本动作 parser 是字节敏感的：CRLF/LF、DSML 变体都会影响动作识别；
- 评测数字必须记录 parser 版本和 sandbox 配置，跨版本不可直接比较。
