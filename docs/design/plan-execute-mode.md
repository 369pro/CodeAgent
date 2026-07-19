# Plan-Execute Mode 运行设计

本文说明 PCode 的 plan mode 在运行时具体怎么触发、允许模型调用哪些工具、如何退出 planning、如何进入 execute，以及一个完整例子。

## 一句话流程

Plan mode 是“先让模型只读探索并写计划文件，再让用户审批，最后恢复正常工具权限执行”的模式。

核心链路是：

```text
用户触发 /plan
  -> PCode 进入 plan mode
  -> 模型只读探索，并只能写 .codeagent/plans/current.md
  -> 模型调用 exit_plan_mode
  -> PCode 停止 agent loop，进入 plan-review 输入态
  -> 用户输入 /do 执行，或输入反馈继续修改计划
  -> 执行时恢复正常工具面，并注入已审批计划
```

## 什么时候触发

PCode 有三种入口：

- `/plan`：只切换到 plan mode，不立刻调用模型。下一条普通用户输入会作为 planning 请求。
- `/plan <task>`：切换到 plan mode，并立即把 `<task>` 发送给模型做 planning。
- plan-review 下的普通文本：计划 ready 后，如果用户没有输入 `/do`，而是输入修改意见，PCode 会回到 plan mode，模型基于已有 `current.md` 增量修改。

进入 plan mode 时，PCode 会准备固定计划路径：

```text
.codeagent/plans/current.md
```

新建 `/plan <task>` 会清空或覆盖旧 `current.md`。单纯 `/plan` 不会立刻清空文件，等用户真正发起 planning 请求时再进入规划流程。

## Plan Mode 里模型能调用哪些工具

plan mode 的工具面是专门收窄过的，不是普通执行工具面。

允许的工具：

- `read_file`：读取文件内容。
- `find_file`：按文件名找文件。
- `grep`：搜索文件内容。
- `glob`：按 glob 找文件。
- `git_status`：查看 git 工作区状态。
- `git_diff`：查看 git diff。
- `write_file`：只能写 `.codeagent/plans/current.md`。
- `exit_plan_mode`：告诉 PCode 计划已经写好，可以退出 planning。

不暴露的工具：

- `bash`。
- `edit_file`。
- 计划文件之外的项目文件写入。
- git mutation 命令，比如 commit、checkout、merge、push。

这里有两层保护：

- **工具可见性保护**：plan mode 的 `ToolRegistry` 不把 `bash` 等执行工具展示给模型。
- **权限保护**：即使模型通过异常路径尝试调用写工具改 `README.md`，`PermissionChecker` 也会拒绝，因为 plan mode 只允许写活动计划文件。

也就是说，plan mode 不是“提醒模型别改代码”这么软，而是工具面和权限层一起约束。

## 每一轮模型请求会带什么提示

plan mode 下，每个 ReAct step 都会注入：

```python
build_plan_mode_reminder(plan_path, plan_exists, step_number)
```

这个 reminder 会告诉模型：

- 当前处于 plan mode。
- 不允许执行实现。
- 只能读项目文件。
- 唯一可写文件是 `.codeagent/plans/current.md`。
- 最后必须调用 `exit_plan_mode`。

如果计划文件已经存在，reminder 会提示模型可以读取并增量修改；如果不存在，会提示模型用 `write_file` 创建它。

## 怎么退出 Plan Mode

退出不是靠模型输出 `Final Answer`，而是靠工具调用：

```text
Action: exit_plan_mode
Action Input: {}
```

`exit_plan_mode` 成功需要满足：

- 当前确实在 plan mode。
- `.codeagent/plans/current.md` 存在。
- 文件内容不是空白。

如果模型在同一轮里先调用 `write_file` 写计划，再调用 `exit_plan_mode`，PCode 会按前一个工具调用后的磁盘文件来判断。只要磁盘上的 `current.md` 已经存在且非空，退出就成功。

成功后，agent loop 立即停止，不再要求模型继续生成 `Final Answer`。`PCodeTurnResult` 会带上：

```python
plan_ready = True
plan_path = ".codeagent/plans/current.md"
```

然后 TUI 读取计划文件内容，进入 `plan-review` 输入态。

## Plan-Review 如何切换到 Execute

计划 ready 后，TUI 不弹出独立审批弹窗，而是恢复底部输入框焦点，并提示：

```text
Plan ready. Type /do to execute, /do --manual for manual permissions, or type feedback to revise.
```

输入 `/do` 时：

- PCode 退出 plan mode。
- 恢复普通工具面。
- 使用当前配置的权限模式。
- 把计划内容注入执行 turn。

输入 `/do --manual` 时：

- PCode 退出 plan mode。
- 恢复普通工具面。
- 权限模式临时使用“当前模式”和 `PermissionMode.DEFAULT` 中更严格的那个。
- 这样不会把 `bypassPermissions`、`dontAsk`、`acceptEdits` 这类宽松模式悄悄带入执行。

输入普通反馈文本时：

- PCode 回到 plan mode。
- 保留现有 `.codeagent/plans/current.md`。
- 把这条反馈作为 revise 请求发给模型。
- 模型继续只读探索或用 `write_file` 更新计划文件。
- 修改完成后，模型再次调用 `exit_plan_mode`，重新进入审批。

输入 `/plan-cancel` 时：

- PCode 清理运行时 `PlanState`。
- 回到普通 do mode。
- 不执行计划。
- 不删除磁盘上的 `current.md`，下一次新 plan session 会清空或覆盖它。

## Execute 阶段发生什么

execute 阶段和 plan mode 的关键区别是：恢复普通工具和普通权限系统。

PCode 会把已审批计划和原始 planning 请求一起注入第一步执行 reminder：

```text
Execute the approved plan below. Keep changes scoped to the plan and verify the result.

Original planning request:
<用户最初要求>

<approved-plan>
<.codeagent/plans/current.md 的内容>
</approved-plan>
```

这样模型执行时会同时看到：

- 用户最初想解决什么问题。
- 刚才审批通过的具体计划。

执行过程中，`.codeagent/plans/current.md` 只是上下文文件，不再有特殊写权限。普通权限系统决定能不能改它。

执行成功结束后，PCode 清理运行时 `PlanState`，但仍保留磁盘上的 `current.md`，方便用户回看。

## 完整例子

假设用户输入：

```text
/plan 给 PCode 加一个 --version 参数
```

### 1. PCode 进入 plan mode

PCode 做三件事：

- 设置 `planning_mode = true`。
- 准备 `.codeagent/plans/current.md`。
- 把 “给 PCode 加一个 --version 参数” 作为 planning 请求发给模型。

### 2. 模型只读探索

模型可能先找 CLI 入口：

```text
Action: find_file
Action Input: {"name": "pcode_cli.py"}
```

然后读文件：

```text
Action: read_file
Action Input: {"path": "src/codeagent/pcode_cli.py"}
```

它也可以看测试：

```text
Action: glob
Action Input: {"pattern": "tests/**/*pcode*"}
```

它不能直接改 `src/codeagent/pcode_cli.py`，因为 plan mode 的工具列表里根本没有 `edit_file`。如果模型试图用 `write_file` 写计划文件之外的路径，权限层会拒绝，agent loop 不终止，模型可以改为继续规划。

### 3. 模型写计划文件

模型把计划写到唯一允许的文件：

```text
Action: write_file
Action Input: {
  "path": ".codeagent/plans/current.md",
  "content": "# Plan\n\n## Context\n需要给 PCode CLI 增加版本输出。\n\n## Scope\n- src/codeagent/pcode_cli.py\n- tests/v2_pcode_tui_agent/test_pcode_cli.py\n\n## Steps\n1. 在 argparse 增加 --version。\n2. 复用 package_version 或统一版本读取逻辑。\n3. 增加 CLI 测试。\n\n## Verification\n- uv run pytest tests/v2_pcode_tui_agent/test_pcode_cli.py\n\n## Risks\n- 注意不要影响 chat/agent 子命令解析。"
}
```

### 4. 模型退出 plan mode

计划文件存在且非空后，模型调用：

```text
Action: exit_plan_mode
Action Input: {}
```

PCode 收到成功结果后：

- 停止当前 agent loop。
- 返回 `plan_ready=True`。
- 读取 `.codeagent/plans/current.md`。
- 进入 `plan-review` 输入态，并把焦点还给底部输入框。

### 5. 用户确认或修改

如果用户输入 `/do`：

- PCode 退出 plan mode。
- 开始一个新的执行 turn。
- 第一轮请求带上 approved plan。
- 模型现在可以按普通权限调用 `edit_file` 修改 `src/codeagent/pcode_cli.py`。

如果用户直接输入反馈：

```text
测试也要覆盖 ./run.sh --version
```

PCode 会回到 plan mode，模型只能修改 `current.md`，把这个测试要求补进计划。补完后再次调用 `exit_plan_mode`，重新审批。

## 两个容易混淆的点

第一，`/do` 的含义取决于当前状态。普通 do mode 下，`/do <text>` 是直接执行一段新文本；plan-review 下，只有 `/do` 或 `/do --manual` 会执行刚写好的计划。plan-review 下的普通文本都被当作 revise 反馈。

第二，`exit_plan_mode` 不上传或返回计划全文。它只发出“计划已完成”的信号。真正的计划内容由 PCode 从 `.codeagent/plans/current.md` 读取。
