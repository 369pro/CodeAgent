# v1 Tools and Run Records Plan

## 架构概览

本版本把当前 `src/codeagent/tools.py` 拆成 `src/codeagent/tools/` 包，形成四层结构：

- 工具协议层：定义 `Tool`、`ToolResult`、`ToolContext`、参数 schema 描述和工具类别。
- 工具实现层：每个工具一个文件，专注自身参数校验、执行和错误包装。
- registry 层：负责注册工具、列出工具、生成 prompt 描述、按名称执行工具。
- 运行记录层：ReActAgent 在每轮模型输出和工具执行后写入内存 trace，运行结束后由 recorder 持久化 JSON。

参考项目 `/Users/tsy/WorkSpace/3_mewcode-python` 中可借鉴的点：

- `mewcode/tools/base.py` 的工具基类、类别和统一 `ToolResult`。
- `mewcode/tools/__init__.py` 的 registry + 默认注册函数。
- `read_file`、`write_file`、`edit_file` 的“先读后写”状态缓存。
- `bash` 的超时、stdout/stderr 捕获和非零退出码错误标记。
- `ConversationManager`、`TraceManager`、`transcript.py` 的结构化记录思路。

不直接迁移的点：

- 不引入 Pydantic 作为必须依赖。
- 不引入 async 工具执行，除非后续 ReAct 循环整体改为 async。
- 不引入 AgentTool、team、task、worktree、MCP、skill 安装工具；本版本只实现核心工具集。

## 核心数据结构

### ToolCategory

```python
ToolCategory = Literal["read", "write", "command"]
```

用于区分工具风险级别。当前版本只用于记录、测试和后续权限系统预留。

### ToolResult

```python
@dataclass(frozen=True)
class ToolResult:
    output: str
    is_error: bool = False
    metadata: dict[str, object] = field(default_factory=dict)
```

说明：

- `output`: 给模型看的 Observation 文本。
- `is_error`: 工具是否失败；失败也会作为 Observation 回填。
- `metadata`: 给 run record 使用，例如 return code、duration、matched count。

### ToolContext

```python
@dataclass
class ToolContext:
    workspace_root: Path
    file_state: FileStateCache
    output_limit: int
```

说明：

- `workspace_root`: 所有本地文件工具的访问根目录。
- `file_state`: 记录 `read_file` 读过的文件快照，供 `write_file`/`edit_file` 校验。
- `output_limit`: 工具输出截断上限。

### Tool

```python
class Tool(Protocol):
    name: str
    description: str
    category: ToolCategory
    parameters: dict[str, object]

    def execute(self, args: dict[str, object], context: ToolContext) -> ToolResult:
        ...
```

说明：

- `parameters` 使用 JSON Schema 风格描述，方便后续切换到 OpenAI function calling。
- 当前 ReAct prompt 仍使用文本协议，但 registry 可以基于 schema 生成可读描述。

### FileStateCache

```python
@dataclass
class FileSnapshot:
    path: Path
    mtime_ns: int
    size: int

class FileStateCache:
    def record_read(self, path: Path) -> None: ...
    def check_writable(self, path: Path) -> tuple[bool, str]: ...
    def update_after_write(self, path: Path) -> None: ...
```

规则：

- 新文件可直接写。
- 覆盖或编辑已有文件前必须先通过 `read_file` 读取。
- 如果读取后文件的 mtime 或 size 变化，拒绝写入，提示重新读取。

### RunRecord

```python
@dataclass
class RunRecord:
    run_id: str
    started_at: str
    ended_at: str | None
    status: Literal["completed", "failed", "max_steps_exceeded"]
    workspace: str
    user_input: str
    final_answer: str | None
    steps: list[RunStep]
    error: str | None = None
```

### RunStep

```python
@dataclass
class RunStep:
    index: int
    llm_output: str
    tool_name: str | None
    tool_input: dict[str, object] | None
    observation: str | None
    is_error: bool = False
    started_at: str
    ended_at: str
```

说明：

- 记录每一步模型输出和工具结果，足够复盘一次 ReAct 决策。
- 不记录 API key，不记录完整环境变量。

## 模块设计

### `codeagent.tools.base`

职责：定义工具协议、工具结果、工具上下文、路径安全辅助函数、输出截断规则。

对外接口：

- `ToolResult`
- `ToolContext`
- `BaseTool`
- `resolve_workspace_path(root, raw_path)`
- `truncate_output(text, limit)`

依赖：标准库。

### `codeagent.tools.registry`

职责：注册工具、查找工具、生成 prompt 描述、执行工具并统一捕获异常。

对外接口：

- `ToolRegistry.register(tool)`
- `ToolRegistry.get(name)`
- `ToolRegistry.names()`
- `ToolRegistry.descriptions()`
- `ToolRegistry.run(name, args, context)`
- `build_default_registry()`

依赖：`codeagent.tools.*`。

### `codeagent.tools.file_state`

职责：支持写入类工具的“先读后写”保护。

对外接口：

- `record_read(path)`
- `check_writable(path)`
- `update_after_write(path)`

依赖：标准库 `pathlib`。

### `codeagent.tools.read_file`

职责：读取文件并返回带行号内容；读取成功后更新 `FileStateCache`。

参数：

- `path: str`
- `offset: int = 0`
- `limit: int = 2000`

### `codeagent.tools.write_file`

职责：写入文件；新文件允许直接写；已有文件必须通过 `FileStateCache` 校验。

参数：

- `path: str`
- `content: str`

### `codeagent.tools.edit_file`

职责：对已读取文件做唯一字符串替换。

参数：

- `path: str`
- `old_string: str`
- `new_string: str`

规则：

- 文件必须存在。
- 文件必须已被读取且未被外部修改。
- `old_string` 必须出现且只出现一次。

### `codeagent.tools.glob`

职责：按 glob pattern 查找文件。

参数：

- `pattern: str`
- `path: str = "."`

规则：

- 跳过 `.git`、`.venv`、`node_modules`、`__pycache__`、`.pytest_cache`。
- 返回相对 workspace 的路径。

### `codeagent.tools.grep`

职责：按正则搜索文件内容。

参数：

- `pattern: str`
- `path: str = "."`
- `include: str = ""`

规则：

- 支持 include 限定文件名，例如 `*.py`。
- invalid regex 返回工具错误，不抛 Python traceback。

### `codeagent.tools.bash`

职责：执行 shell 命令并返回 stdout/stderr。

参数：

- `command: str`
- `timeout: int = 120`

规则：

- timeout 最大值 600 秒。
- 非零退出码返回 `is_error=True`。
- 超时后终止进程并返回错误。
- 工作目录固定为 workspace root。

### `codeagent.tools.git_status`

职责：只读查看 git 工作区状态。

参数：

- `short: bool = True`

执行：

- 默认等价于 `git status --short`。

### `codeagent.tools.git_diff`

职责：只读查看 git diff。

参数：

- `cached: bool = False`
- `path: str = ""`

执行：

- 默认等价于 `git diff`。
- `cached=True` 时等价于 `git diff --cached`。

### `codeagent.records`

职责：定义 run record 数据结构和 JSON 持久化。

对外接口：

- `RunRecorder.start(user_input, workspace)`
- `RunRecorder.record_step(...)`
- `RunRecorder.complete(final_answer)`
- `RunRecorder.fail(error, status)`
- `RunRecorder.save() -> Path`

默认目录：

```text
.codeagent/runs/YYYYMMDD-HHMMSS-<run_id>.json
```

### `codeagent.agent`

职责：接入新的 registry 和 recorder。

变化：

- `ReActAgent.run()` 初始化 `RunRecorder`。
- 每次 LLM 输出后记录 step。
- 每次工具执行后记录 Observation、错误状态、耗时。
- 结束时返回 `RunResult(answer, steps, record_path)`。

### `codeagent.cli`

职责：交互模式展示最终回答和记录路径。

行为：

```text
codeagent> ...
<answer>
Run record: .codeagent/runs/20260717-153012-a1b2c3d4.json
```

## 模块交互

```text
CLI
  -> load_config
  -> build_default_registry
  -> create ToolContext
  -> ReActAgent.run(user_input)
       -> RunRecorder.start
       -> LLM.complete(messages)
       -> parse Action
       -> ToolRegistry.run(name, args, context)
            -> concrete Tool.execute(args, context)
            -> ToolResult
       -> RunRecorder.record_step
       -> append Observation
       -> repeat until Final Answer
       -> RunRecorder.complete/fail
       -> RunRecorder.save
  -> print answer and record path
```

## 文件组织

```text
src/codeagent/
├── agent.py
├── cli.py
├── config.py
├── llm.py
├── records.py
└── tools/
    ├── __init__.py
    ├── base.py
    ├── registry.py
    ├── file_state.py
    ├── read_file.py
    ├── write_file.py
    ├── edit_file.py
    ├── glob.py
    ├── grep.py
    ├── bash.py
    ├── git_status.py
    └── git_diff.py

tests/
├── v0_react_mvp/
│   ├── test_cli.py
│   └── test_config.py
└── v1_tools_and_run_records/
    ├── test_tools.py
    ├── test_records.py
    └── test_react_agent.py
```

文档组织：

```text
docs/
├── specs/
│   ├── v0-react-mvp/
│   │   └── plan.md
│   └── v1-tools-and-run-records/
│       ├── spec.md
│       ├── plan.md
│       └── checklist.md
```

## 技术决策

| 决策点 | 选择 | 理由 |
| --- | --- | --- |
| 工具组织 | 独立 `codeagent.tools` 包 | 工具数量增加后单文件不可维护；也方便单独测试和后续权限系统接入。 |
| 工具命名 | 使用小写 snake_case，如 `read_file` | 延续当前项目已有工具名，减少 prompt、测试和用户习惯迁移成本。 |
| 参数 schema | 手写 JSON Schema 风格 dict | 当前项目不需要为了 schema 增加 Pydantic 依赖；后续可无痛替换。 |
| 执行模型 | 继续同步执行 | 当前 ReActAgent 是同步结构，先减少迁移面；`bash` 可用 `subprocess.run(timeout=...)`。 |
| 写入保护 | 先读后写 + mtime/size 校验 | 借鉴参考项目，能防止模型盲写和外部修改覆盖。 |
| run record 格式 | JSON 文件 | 易读、易 diff、易被后续脚本和评估系统消费。 |
| run record 目录 | `.codeagent/runs/` | 属于本地运行状态，与本地 config 放在一起，并默认 git ignore。 |
| git 工具范围 | 只做 `git_status`/`git_diff` | commit/push 需要权限和作用域确认，先不做可变更远端状态的工具。 |

## Spec 覆盖说明

- F1-F3 由 `tools/base.py`、`tools/registry.py` 覆盖。
- F4-F10 由各具体工具模块覆盖。
- F11 由 `ToolRegistry.run()` 和 `ReActAgent.run()` 的错误回填覆盖。
- F12-F14 由 `records.py`、`RunResult.record_path`、CLI 输出覆盖。

## 已确认决策

- D1: 本版本只实现核心工具集，不实现参考项目中的 team/task/worktree/skill 相关工具。
- D2: 工具名继续使用当前小写 snake_case 风格。
- D3: `.codeagent/runs/` 作为默认运行记录目录。
