# CodeAgent 与 Pico 提示词组装对比

本文对比的是当前 CodeAgent 的 Prompt Bundle 设计，以及 `/Users/tsy/WorkSpace/LLMproj/pico/` 项目里的 Pico prompt 组装源码。

## 结论

CodeAgent 当前走的是 provider-aware 的 Prompt Bundle：把稳定系统提示、动态环境、临时 reminder、历史消息拆开，目标是让稳定前缀可缓存、动态信息不污染缓存。

Pico 走的是 context orchestration：最终仍组装成一段完整 prompt，但这段 prompt 由 prefix、memory、skills、relevant memory、history、current request 多个 section 组成，并且每个 section 有预算、裁剪、压缩和 metadata。

一句话：

- CodeAgent 现在更像“缓存友好的请求分层”。
- Pico 更像“长任务上下文治理系统”。

## 入口对比

### CodeAgent

核心入口：

- `src/codeagent/prompts.py`
- `src/codeagent/pcode_agent.py`
- `src/codeagent/chat.py`

调用链：

```text
PCodeAgentSession._stream_llm_output()
  -> build_stable_prompt(tools)
  -> build_environment_block(workspace, model)
  -> planning_reminder() / execute_plan_reminder()
  -> ChatRequest(stable_prompt, environment, reminders, messages)
  -> ProviderStreamingClient.stream()
```

Provider 层会根据协议翻译：

- OpenAI-compatible: stable prompt 和 environment 都作为 system message。
- Anthropic-compatible: stable prompt 作为第一个 system content block，并加 `cache_control`；environment 作为第二个 system content block。

### Pico

核心入口：

- `/Users/tsy/WorkSpace/LLMproj/pico/pico/core/runtime.py`
- `/Users/tsy/WorkSpace/LLMproj/pico/pico/core/context_orchestrator.py`
- `/Users/tsy/WorkSpace/LLMproj/pico/pico/core/context_manager.py`
- `/Users/tsy/WorkSpace/LLMproj/pico/pico/core/context_sections.py`

调用链：

```text
Pico.prompt(user_message)
  -> Pico._build_prompt_and_metadata(user_message)
  -> refresh_prefix()
  -> evaluate_resume_state()
  -> ContextOrchestrator.snapshot(...)
  -> ContextOrchestrator.build(snapshot)
  -> ContextManager.build(user_message)
  -> assemble sections into one prompt string
```

Pico 最终给模型的是一个大 prompt 字符串，同时返回很丰富的 metadata，用于 trace、report、auto compact 判断。

## 组装结构差异

### CodeAgent 的 Prompt Bundle

CodeAgent 现在分四块：

```text
stable_prompt
environment
reminders
messages
```

`stable_prompt` 内部再由 `PromptSection + PromptBuilder` 组装：

```text
Identity
System
DoingTasks
ExecutingActions
UsingTools
ToneStyle
TextOutput
CodeAgentToolProtocol
AvailableTools
CustomInstructions?
Skills?
Memory?
```

特点：

- stable prompt 跨轮保持字节稳定。
- environment 单独动态生成，包含 cwd、OS、当前时间、git 状态、app version、model。
- reminders 是一次请求临时注入，不写入持久 history。
- messages 保留真实对话历史，不在 prompt builder 里裁剪。

### Pico 的 section prompt

Pico 的 section 顺序定义在 `context_sections.py`：

```python
SECTION_ORDER = (
    "prefix",
    "memory",
    "skills",
    "relevant_memory",
    "history",
    "current_request",
)
```

最终 prompt 形状：

```text
prefix

memory

skills

relevant_memory

history

current_request
```

每个 section 都有预算：

- `prefix`: 稳定工作手册、工具协议、workspace context。
- `memory`: working memory、todo ledger、checkpoint、memory system contract。
- `skills`: 可用 skill 列表。
- `relevant_memory`: 根据当前请求检索出的短期/长期记忆。
- `history`: 经过 turn-aware 压缩的 transcript。
- `current_request`: 当前用户请求，不参与裁剪。

## Prefix 差异

CodeAgent 的 stable prompt 是较完整的行为规范，强调：

- 角色身份是 CodeAgent。
- 输出、工具、安全、执行边界。
- 工具调用协议是 `Action: ... Action Input: ...`。
- 工具定义按名称排序，用 canonical JSON 渲染。

Pico 的 prefix 在 `runtime.py::build_prefix()` 中生成，强调：

- 角色身份是 `pico`。
- 工具调用协议是 XML/JSON 混合：

```text
<tool>{"name":"tool_name","args":{...}}</tool>
<tool name="write_file" path="file.py"><content>...</content></tool>
<final>your answer</final>
```

- 工具展示包含 schema、risk、安全等级。
- prefix 包含 `runtime_mode_text()`，所以 plan mode 会直接改变 prefix。
- prefix 包含 `workspace.text()`，即 workspace context 是 prefix 的一部分。

关键区别：CodeAgent 为了缓存，把 environment 从 stable prompt 拆出去；Pico 的 prefix 把 workspace facts 合在一起，但通过 `prefix_hash`、`workspace_fingerprint`、`tool_signature` 判断是否刷新。

## Memory 差异

CodeAgent 目前只有预留槽：

```python
custom_instructions
skill_section
memory_section
```

也就是说，CodeAgent 的 prompt 框架允许接 memory，但当前没有 Pico 那样完整的记忆系统。

Pico 的 memory 是 prompt 组装主角之一：

- `memory_text()` 渲染 working memory。
- `todo_ledger.render_prompt()` 合入 memory section。
- `render_checkpoint_text()` 合入 memory section。
- `build_memory_system_section(memory_dir)` 合入 memory section。
- `memory.retrieval_candidates(user_message, limit=3)` 生成 `Relevant memory:`。

这导致 Pico 每轮 prompt 都是围绕“当前任务状态 + 可检索记忆 + 最近历史”动态生成的。

## History 差异

CodeAgent 当前把 `session.history` 原样交给 provider 适配层，只是在 reminder 注入时把 reminder 合并到最后一个 user message 前面。

Pico 有专门的 `TurnHistoryBuilder`：

- 按 turn_id 分组。
- 最近 turn 保留更多内容。
- 旧工具结果会摘要化。
- 重复 read_file 会折叠。
- 大工具输出可以替换成 artifact 引用。
- 在高上下文压力下减少 recent window 和每行长度。

所以 Pico 的 history 不是简单消息数组，而是一个 prompt-ready transcript。

## 预算与压缩差异

CodeAgent 当前没有上下文预算治理。它只保证 stable prompt 可缓存，尚未对 history、tools、memory 做预算裁剪。

Pico 有完整预算系统：

- `compute_budget_chars(context_window_tokens)` 根据模型上下文窗口算总预算。
- `compute_section_budgets(total_budget_chars)` 按比例给 section 分配预算。
- `REDUCTION_ORDER = ("relevant_memory", "skills", "history", "memory", "prefix")`。
- prompt 压力分为：
  - `tier0_observe`
  - `tier1_snip`
  - `tier2_prune`
  - `tier3_summary`

如果 prompt 超预算，Pico 会先压缩 section；如果仍然压力过高，`ContextOrchestrator` 会触发 auto compact。

## Metadata 差异

CodeAgent 的 metadata 主要服务 Langfuse 和 run record：

- prompt 输入
- message_count
- reminder_count
- token usage
- tool metadata

Pico 的 prompt metadata 更像“上下文治理审计报告”：

- prefix_chars
- workspace_chars
- memory_chars
- history_chars
- request_chars
- prefix_hash
- prompt_cache_key
- workspace_fingerprint
- tool_signature
- resume_status
- stale_paths
- context_usage
- context_orchestrator decision
- auto_compaction_plan
- auto_compaction_summary

这也是两者定位差异：CodeAgent 还在 ReAct MVP + prompt cache 阶段，Pico 已经进入长会话治理阶段。

## Plan Mode 差异

CodeAgent 当前 plan mode 是轻量版：

- `/plan` 只切换 TUI 本地状态，不发给模型。
- PCode agent 在 planning mode 下使用 `tools.read_only()`。
- 每个 ReAct step 注入 `planning_reminder(step_number)`。
- 当前没有真实 plan 文件和 ExitPlanMode 工具。

Pico 的 plan mode 更完整：

- `PlanModeManager.enter(topic, path)` 会写入 session runtime mode。
- plan 文件固定在 `.pico/plans/` 下。
- `set_tool_profile("plan")` 切换工具 profile。
- `refresh_prefix(force=True)` 强制刷新 prefix，让 plan mode 文案和工具面进入 prompt。
- `can_finish()` 要求 active plan artifact 存在且非空。
- `final_notice()` 会阻止没写 plan 文件就 final。

所以 Pico 的 plan mode 是“session 持久状态 + tool profile + plan artifact gate”；CodeAgent 当前是“本地模式位 + read-only registry + runtime reminder”。

## 工具协议差异

CodeAgent：

```text
Action: read_file Action Input: {"path": "README.md"}
Final Answer: ...
```

优点是简单，适合最小 ReAct loop；缺点是多工具、多行内容、嵌套结构会比较脆。

Pico：

```text
<tool>{"name":"read_file","args":{"path":"README.md","start":1,"end":80}}</tool>
<tool name="patch_file" path="file.py"><old_text>...</old_text><new_text>...</new_text></tool>
<final>Done.</final>
```

优点是更适合多工具和多行 patch/write；缺点是解析器和协议复杂度更高。

## 对 CodeAgent 的启发

短期不建议直接照搬 Pico 全套，因为当前 CodeAgent 还需要保持轻量和可验证。更合理的演进顺序：

1. 保留 Prompt Bundle 边界，继续让 stable prompt 和 environment 分离。
2. 引入 Pico 的 section budget 思路，但先只治理 `messages/history`。
3. 把 `memory_section` 从预留槽升级成明确的 working memory section。
4. 后续再加 `relevant_memory` 检索，不要一开始就做 durable memory/dream。
5. Plan mode 可以借 Pico 的 artifact gate：进入 `/plan` 后要求写 `docs/specs/.../plan.md` 或 `.codeagent/plans/...`，否则不能 final。
6. 工具协议如果要升级，优先从当前 `Action Input` 正则换成更结构化的 JSON/XML tool block，而不是继续扩大正则。

## 最大本质差异

CodeAgent 当前的提示词设计中心是：

> 怎样把一次 LLM 请求拆成稳定、动态、临时、历史四层，让 provider cache 和 observability 更清楚。

Pico 的提示词设计中心是：

> 怎样在长会话里控制上下文成本，并让 memory、skills、history、checkpoint、workspace facts 按预算进入模型。

这两个方向不冲突。CodeAgent 后续最好的路线不是抛弃 Prompt Bundle 去复制 Pico，而是在 Prompt Bundle 内部引入 Pico 的 context section / budget / memory retrieval 思想。

## 哪种策略较好

如果只问“哪种更高级”，Pico 更完整；但如果问“当前 CodeAgent 该用哪种”，更好的策略是以 CodeAgent 的 Prompt Bundle 为主线，选择性吸收 Pico 的上下文治理。

原因有三点。

第一，CodeAgent 现在的首要问题是让 ReAct loop、工具调用、provider cache、Langfuse 观测稳定下来。Prompt Bundle 的边界非常清楚：stable prompt 负责长期规则和工具定义，environment 负责动态运行环境，reminders 负责本轮临时约束，messages 负责对话历史。这个结构简单，可测试，也更容易定位“为什么 Langfuse 里每轮 prompt 不一样”。

第二，Pico 的策略适合长会话和复杂任务，但它的系统成本明显更高。它需要 memory state、retrieval、context budget、section floor、turn history renderer、compact manager、checkpoint、artifact replacement、prompt metadata 等一整套配套机制。直接搬到当前 CodeAgent，会让项目从 MVP 跨到复杂上下文治理，代码量和调试面都会暴涨。

第三，两者真正冲突的点不多。CodeAgent 不需要放弃 Prompt Bundle，也能引入 Pico 的优点。正确演进方式是：

```text
Prompt Bundle 外层不变
  stable_prompt
  environment
  reminders
  messages/context

messages/context 内部逐步 Pico 化
  history budget
  working memory
  relevant memory
  compact summary
```

所以推荐判断是：

| 场景 | 更适合的策略 |
| --- | --- |
| 当前 CodeAgent MVP / PCode TUI | CodeAgent Prompt Bundle |
| 需要 provider cache、Langfuse 对比、稳定调试 | CodeAgent Prompt Bundle |
| 长会话、多文件任务、反复读文件成本高 | Pico context orchestration |
| 需要跨 session 记忆、checkpoint、自动压缩 | Pico context orchestration |
| CodeAgent 下一阶段演进 | Prompt Bundle + Pico 式 section budget |

我的建议：

当前不要把 Pico 的大 prompt orchestrator 整体搬进来。应该保持 CodeAgent 的四层 Prompt Bundle，然后先补三件 Pico 思路：

1. 对 `messages/history` 加预算和裁剪。
2. 增加轻量 working memory，记录当前任务和最近读过的文件摘要。
3. 给 plan mode 增加真实 plan artifact gate。

这条路线比较稳：不会破坏现有 provider cache 设计，又能逐步解决长对话上下文膨胀的问题。
