# File State Cache Design

## 背景

CodeAgent 的工具系统允许模型读取、写入和编辑工作区文件。读文件是低风险动作，但写入和编辑会改变用户代码。模型如果没有先看过文件内容，就直接覆盖文件，很容易造成两个问题：

- 盲写：模型不知道当前文件真实内容，只凭猜测生成新内容。
- 覆盖并发修改：用户或其他进程在模型读取之后改了文件，模型再写入会把外部修改抹掉。

`FileStateCache` 的设计目标不是做完整版本控制，而是在工具层加一道轻量的安全闸门：模型必须先通过 `read_file` 建立对文件当前状态的观察，然后才能用 `write_file` 或 `edit_file` 修改已有文件。

## 核心理念

### 1. 写入必须基于观察

Agent 的 ReAct 循环强调“先观察，再行动”。这个原则落到文件工具上，就是修改已有文件前必须先读取文件。

`read_file` 成功后会调用 `record_read(path)`，记录该文件的快照。`write_file` 和 `edit_file` 修改已有文件前会调用 `check_writable(path)`。如果没有快照，就拒绝修改。

这让模型不能直接说“把 app.py 改成这样”就覆盖文件。它必须先看见文件内容，后续编辑才有上下文依据。

### 2. 快照只记录能判断状态变化的最小信息

当前快照只保存：

- `mtime_ns`: 文件最后修改时间，纳秒级。
- `size`: 文件大小。

不保存完整内容，是为了让这个组件保持轻量。它只负责回答一个问题：这个文件自上次读取以来是否看起来变过？

如果 `mtime_ns` 或 `size` 任何一个变化，就认为文件状态已过期，要求重新读取。

### 3. 新文件可以直接创建

`check_writable(path)` 对不存在的文件返回允许。原因是新文件没有“覆盖已有用户内容”的风险，也没有需要先观察的旧状态。

这个规则让工具既安全又不笨重：创建新文件不需要额外绕一圈 `read_file`。

### 4. 写入成功后刷新快照

`write_file` 或 `edit_file` 成功后会调用 `update_after_write(path)`，把新文件状态记录为最新快照。

这样同一轮 ReAct 里可以连续做多个编辑：

```text
read_file -> edit_file -> edit_file
```

第一次编辑成功后，cache 会承认编辑后的文件就是当前已观察状态，第二次编辑不需要重新读取。

## 数据结构

```python
@dataclass(frozen=True)
class FileSnapshot:
    mtime_ns: int
    size: int
```

```python
class FileStateCache:
    def record_read(self, path: Path) -> None: ...
    def check_writable(self, path: Path) -> tuple[bool, str]: ...
    def update_after_write(self, path: Path) -> None: ...
```

内部用 resolved absolute path 做 key，避免同一个文件通过不同相对路径被记录成多个状态。

## 行为规则

### `record_read`

读取文件的当前 `stat()` 信息，记录 `mtime_ns` 和 `size`。

调用方：`read_file`

### `check_writable`

用于写入已有文件前检查。

规则：

- 文件不存在：允许写入。
- 文件存在但没有快照：拒绝，提示先调用 `read_file`。
- 文件存在且有快照，但 `mtime_ns` 或 `size` 变化：拒绝，提示重新读取。
- 文件存在且快照一致：允许写入。

调用方：`write_file`、`edit_file`

### `update_after_write`

写入成功后重新记录当前文件状态。

调用方：`write_file`、`edit_file`

## 为什么不用完整内容 hash

内容 hash 更准确，但当前阶段先用 `mtime_ns + size`，原因是：

- 标准库实现简单，不需要读取完整文件。
- 对 code agent 常见文件规模足够实用。
- 这个 cache 是安全提示层，不是版本控制系统。

后续如果遇到文件系统 mtime 精度不足、外部工具保留 mtime、或需要更强一致性，可以扩展为：

```text
mtime_ns + size + optional content_hash
```

## 与 Git 的关系

`FileStateCache` 不替代 git，也不判断“这个改动是否应该提交”。它只判断一次 ReAct 运行内部，工具是否在基于已观察状态修改文件。

Git 负责版本历史和提交边界；`FileStateCache` 负责工具调用时的局部写入保护。

## 边界

- 只在当前进程内有效，重启 CLI 后 cache 会丢失。
- 只保护通过工具执行的写入，不拦截用户在编辑器里的修改。
- 不能证明文件内容一定没变，只能用快照判断“状态是否看起来一致”。
- 对新文件允许直接创建。

## 设计取舍

| 选择 | 好处 | 代价 |
| --- | --- | --- |
| 先读后写 | 强制模型基于真实上下文编辑 | 有些简单覆盖需要多一次读取 |
| `mtime_ns + size` | 轻量、快、标准库即可实现 | 不如内容 hash 严格 |
| 新文件直接写 | 创建文件流程简单 | 文件路径写错时仍可能创建新文件 |
| 写后刷新快照 | 支持连续编辑 | 如果希望每次编辑都强制重新读，需要更严格模式 |

## 后续演进

- 增加可选 content hash，用于更强一致性检查。
- 在 run record 中记录文件状态检查结果，方便复盘为什么某次编辑被拒绝。
- 配合权限系统，对写入类工具增加确认策略。
- 配合 git 工具，在写入前提示当前工作区是否已有未提交变更。
