# Agent Hooks Implementation Plan

本计划对应 `docs/specs/v6-agent-hooks/spec.md`。已确认的实现原则是：硬安全边界在 Hook 之前，项目自动化策略在 Hook 里，普通权限确认在 Hook 之后；matcher 底层统一，配置语法渐进迁移。

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
- 集中校验 YAML 并收集 warning。
- 实现 session 内 `once` 状态，不写磁盘。
- 跳过非法规则，不让配置错误阻断 Agent 启动。

## Phase 3: Hook Executor

- command action:
  - workspace root 作为 cwd。
  - 事件上下文 JSON 写入 stdin。
  - 支持 `timeout_seconds`。
  - stdout/stderr 截断后进入 Hook Result。
- HTTP action:
  - 事件上下文 JSON 作为 request body。
  - 支持 method、headers、timeout。
  - 非 2xx 记为 Hook 失败。
- prompt action:
  - `message.before_model` 产出 reminder。
  - `tool.before` block 时产出拒绝原因。
  - 在 `tool.before` 上不静默修改后续模型上下文。
- subagent action:
  - 只返回 placeholder result。

## Phase 4: Agent Loop Integration

- `turn.start`: 收到用户输入时触发。
- `message.before_model`: 组装 Prompt Bundle 前收集 prompt reminders。
- `message.after_model`: 模型输出完成后触发，用于记录和后续扩展。
- hard safety: 工具名和参数解析成功后，先执行危险命令黑名单和路径沙箱硬检查。
- `tool.before`: 硬安全检查通过后触发；成功 block 时跳过普通权限判断和工具执行。
- permission policy: `tool.before` 未阻断后，再执行 YAML 权限规则、权限模式和 HITL。
- `tool.after`: 工具成功返回后触发。
- `tool.error`: 工具返回 `is_error=true` 后触发。
- `turn.end`: 最终回答、失败或 max steps 后触发。

Implementation note:

- 当前 `PCodeAgentSession` 在调用 `ToolRegistry.run` 前已经提前做了一次权限 ask 检查，而 `ToolRegistry.run` 内部还会再检查一次。接 Hook 时应顺手整理这个边界，否则 `tool.before`、权限 ask、工具执行之间的顺序会难测。

## Phase 5: Records And Observability

- Run Record metadata 记录 Hook Result 摘要。
- Langfuse / tracer 层记录 hook span 或将 hook metadata 附到 turn/tool observation。
- Hook 失败只进日志和 metadata，不写成模型 Observation。
- Hook block 作为工具错误 Observation 回填给模型。
- Hook Result 第一版只写入 Run Record metadata，不新增顶层 schema 字段。

## Phase 6: Tests

- YAML loader:
  - 合法配置能加载。
  - 非法配置产生 warning 并跳过。
  - `background: true` 在 `tool.before` 上非法。
- Matcher:
  - `all`、`any`、`not`、`regex`、`glob`。
  - 工具参数和 normalized path 匹配。
- Executor:
  - command stdin、cwd、timeout。
  - HTTP success/failure。
  - prompt reminder 注入。
  - subagent placeholder。
- Agent loop:
  - 硬安全检查先于 `tool.before`。
  - `tool.before` block 不执行工具。
  - block reason 进入 Observation。
  - Hook failure 不影响最终回答。
  - `once` 当前 session 内只执行一次。

## Confirmed Checklist

- [x] `tool.before` 在权限硬拦截之后、普通权限判断之前执行。
- [x] 扩展 matcher 底层同步，权限 YAML 表面语法渐进迁移。
- [x] `tool.after` 自动格式化第一版立即后台执行。
- [x] 非工具事件第一版只支持无条件和 `planning_mode`、`model`、`cwd`。
- [x] Hook Result 第一版放入 Run Record metadata。
