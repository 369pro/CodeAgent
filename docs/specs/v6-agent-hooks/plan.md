# Agent Hooks Implementation Plan

本计划对应 `docs/specs/v6-agent-hooks/spec.md`。已确认的实现原则是：硬安全边界在 Hook 之前，项目自动化策略在 Hook 里，普通权限确认在 Hook 之后；matcher 底层统一，配置语法渐进迁移。

## Implementation Prerequisite

- 真正开始实现前，先 checkout 到主分支，再从主分支创建本轮实现分支。
- 不从当前带有无关改动的工作区直接开始实现。

## Phase 1: Shared Matcher

- 从 `codeagent.permissions.rules` 抽出共享 matcher 模块。
- 保留现有 `ToolName(pattern)` 行为，确保 v4 权限测试不回退。
- 增加条件节点：`rule`、`not`、`regex`、`glob`。
- 实现 `all` / `any` 二选一组合校验。
- 为 tool primary subject、normalized path subject、unknown tool subject 增加单元测试。
- 权限 YAML 第一版保持旧格式；Hook YAML 先使用结构化 matcher。

Compatibility target:

- 权限规则和 Hook 条件共用同一个底层 matcher。
- 现有 `.codeagent/permissions.yaml` 不需要迁移。
- 后续版本再决定是否让权限 YAML 接受 Hook 的结构化 matcher 语法。

## Phase 2: Hook Data Model And Loader

- 新建 `src/codeagent/hooks/` 包。
- 定义 `HookRule`、`HookCondition`、`HookAction`、`HookEvent`、`HookResult`。
- 加载 `~/.codeagent/hooks.yaml`、`.codeagent/hooks.yaml`、`.codeagent/hooks.local.yaml`。
- 标记配置来源：user global、project shared、project local。
- 对 project shared 配置默认禁用 command/http，记录 warning 并跳过。
- 集中校验 YAML 并收集 warning。
- 实现 session 内 `once` 状态，不写磁盘。
- 跳过非法规则，不让配置错误阻断 Agent 启动。

## Phase 3: Hook Executor

- command action:
  - workspace root 作为 cwd。
  - 只接受 `argv` 数组，不接受 shell 字符串。
  - 最小化事件上下文 JSON 写入 stdin。
  - 支持 `timeout_seconds`。
  - stdout/stderr 截断后进入 Hook Result。
  - 受硬安全检查约束。
- HTTP action:
  - 最小化事件上下文 JSON 作为 request body。
  - 只允许 `https://` URL。
  - 支持 method、headers、timeout。
  - 非 2xx 记为 Hook 失败。
  - 不支持环境变量拼 URL，不自动附带鉴权 header。
- prompt action:
  - `message.before_model` 产出带 `<hook-reminder>` 标签的内部 reminder。
  - `<hook-reminder>` 明确要求模型只调整行为，不引用、总结、确认，也不把它当成工具结果或用户请求。
  - `tool.before` block 时产出拒绝原因。
  - 在 `tool.before` 上不静默修改后续模型上下文。
- subagent action:
  - 只返回 placeholder result。
- background:
  - 第一版不 drain，只记录 started metadata。
  - 只允许 `turn.end` 或低风险 telemetry。
  - 格式化类 Hook 用 `tool.after` 同步短超时，或 `turn.end` 后台执行。

## Phase 4: Agent Loop Integration

- Hook 只接入 `PCodeAgentSession._run_turn_with_recording()`；旧 `ReActAgent` 第一版不触发 Hook。
- `turn.start`: `RunRecorder.start(user_input)` 之后、用户消息写入 history 之前触发。
- `message.before_model`: 每个 Agent Loop Step 中，内置 reminders 生成之后、`_stream_llm_output(...)` 之前触发；prompt action 只追加本次 generation 的 tagged hook reminders。
- `message.after_model`: 模型流式输出结束后、解析 Action / Final Answer 之前触发。
- invalid action input: Action Input 不是合法 JSON object 时，不触发 `tool.before`、`tool.after` 或 `tool.error`，只把 invalid-input Observation 回填给模型。
- hard safety: 工具名和参数解析成功后，先执行危险命令黑名单和路径沙箱硬检查；硬拦截不进入 `tool.before`。
- `tool.before`: 硬安全检查通过后触发；成功 block 时跳过普通权限判断和工具执行。
- blocking hooks: 多个 Hook 命中时按执行顺序返回第一个 block reason，并停止后续 blocking Hook。
- permission policy: `tool.before` 未阻断后，再执行 YAML 权限规则、权限模式和 HITL；普通权限 deny/ask deny 不触发 `tool.after` 或 `tool.error`。
- tool execution: 普通权限通过后调用 `ToolRegistry.run_prechecked(...)`，避免重复权限检查。
- `tool.after`: 真实工具执行成功且 `is_error=false` 后触发。
- `tool.error`: 真实工具执行完成且 `is_error=true` 后触发。
- `turn.end`: 正常 final answer、plan ready、max steps exceeded 三类保存 run record 的终止路径触发；未预期异常路径当前只保存失败记录，不触发 `turn.end`。
- Hook Result: 通过 `RunRecorder.record_hook_results(...)` 写入 `RunRecord.metadata["hooks"]`，不进入模型 history。

Implementation note:

- 已通过 `PermissionChecker.check_hard_safety(...)`、`PermissionChecker.check_policy(...)` 和 `ToolRegistry.run_prechecked(...)` 整理权限边界。
- 当前顺序是硬安全检查先于 `tool.before`，普通权限判断晚于 `tool.before`，真实工具执行只走 `run_prechecked(...)`。

## Phase 5: Records And Observability

- Run Record metadata 记录 Hook Result 摘要。
- 第一版不新增独立 Langfuse / tracer hook span；后续可将 hook metadata 附到 turn/tool observation。
- Hook 失败只进日志和 metadata，不写成模型 Observation。
- Hook block 作为工具错误 Observation 回填给模型。
- Hook Result 第一版只写入 Run Record metadata，不新增顶层 schema 字段。

## Phase 6: Tests

- YAML loader:
  - 合法配置能加载。
  - 非法配置产生 warning 并跳过。
  - `background: true` 在 `tool.before` 上非法。
  - project shared 配置中的 command/http 被跳过并产生 warning。
  - command action 使用 `command` 字段非法，必须使用 `argv`。
- Matcher:
  - `all`、`any`、`not`、`regex`、`glob`。
  - 工具参数和 normalized path 匹配。
- Executor:
  - command argv、stdin、cwd、timeout、硬安全检查。
  - HTTP HTTPS-only、success/failure。
  - 最小化事件上下文不包含 history、完整模型输出、环境变量或文件内容。
  - prompt reminder 注入。
  - subagent placeholder。
- Agent loop:
  - 硬安全检查先于 `tool.before`。
  - `tool.before` block 不执行工具。
  - 多个 blocking Hook 命中时只返回第一个 reason。
  - block reason 进入 Observation。
  - Hook failure 不影响最终回答。
  - `once` 当前 session 内只执行一次。
  - background Hook 不 drain。

## Confirmed Checklist

- [x] `tool.before` 在权限硬拦截之后、普通权限判断之前执行。
- [x] 扩展 matcher 底层同步，权限 YAML 表面语法渐进迁移。
- [x] `tool.after` 自动格式化第一版立即后台执行。
- [x] 非工具事件第一版只支持无条件和 `planning_mode`、`model`、`cwd`。
- [x] Hook Result 第一版放入 Run Record metadata。
- [x] Hook command/http 有自己的执行权限边界，不走模型工具 HITL。
- [x] project shared `.codeagent/hooks.yaml` 默认禁止 command/http。
- [x] command action 第一版只接受 `argv` 数组。
- [x] HTTP action 第一版只允许 trusted 配置层和 `https://` URL。
- [x] Hook 事件上下文默认最小化。
- [x] background Hook 第一版不 drain，并收窄使用场景。
- [x] 多个 blocking Hook 命中时返回第一个 reason。
