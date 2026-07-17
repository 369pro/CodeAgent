# v3 Langfuse 可观测性规格

## 背景

CodeAgent 已经会把本地 JSON 运行记录写入 `.codeagent/runs/`。这些记录适合事后排查，但还没有在线可观测视图，无法方便地查看延迟、模型调用与工具调用的嵌套关系，也不利于后续做评估工作流。

本版本加入 Langfuse tracing，先服务第一个可观测目标：调试单次代理轮次。已经确认的 trace 边界是一次用户输入，而不是一次工具调用。工具调用和模型调用都作为同一个 trace 下的子 observation，这样能保留完整因果顺序。

目标 SDK 是 Langfuse Python SDK v4。它支持通过 `get_client()` 获取客户端，并用 `start_as_current_observation(...)` 手动创建 observation。短生命周期 CLI 程序结束前需要 flush。

## 目标

为现有 ReAct CLI 和 PCode TUI agent loop 增加可选的 Langfuse tracing，不替换本地运行记录，也不改变当前 ReAct 文本协议。

核心结果：当配置了 Langfuse 凭证后，用户可以在 Langfuse 中打开一次完整代理轮次，查看其下嵌套的模型 generation 和工具 span。

## 功能需求

- F1: 每次 `ReActAgent.run(user_input)` 必须为该用户输入创建一个运行追踪。
- F2: 每次 `PCodeAgentSession.run_turn(user_input)` 必须为该用户输入创建一个运行追踪。
- F3: 被追踪轮次中的每次模型调用都必须记录为 generation observation。
- F4: 被追踪轮次中每个成功解析出的工具调用都必须记录为子 span。
- F5: 工具 span 必须包含工具输入、工具输出、错误状态和可用的工具元数据。
- F6: generation observation 必须包含已知模型名、请求消息和最终模型文本。
- F7: 运行追踪必须包含 workspace 路径，以及最终回答或失败信息。
- F8: 本地 JSON 运行记录必须继续按现有 schema 写入。
- F9: Langfuse tracing 必须是可选能力，由环境变量配置控制。
- F10: 缺少 Langfuse 凭证或缺少 SDK import 时，普通 agent 执行不能被影响。
- F11: CLI 这类短生命周期运行在完成或失败后必须 flush tracing 数据。

## 非功能需求

- N1: agent loop 只能依赖项目内的小型 tracing 抽象，不应散落 Langfuse SDK 调用。
- N2: 测试不能依赖真实 Langfuse 凭证、网络访问或 Langfuse 服务。
- N3: 现有 fake LLM 和 fake streaming 测试必须继续工作。
- N4: 第一版不主动做 token 或 cost 统计，除非 provider 响应已经暴露这些数据。
- N5: API key 和 provider secret 不得写入 trace metadata。
- N6: Langfuse 相关错误不能掩盖原始 agent 结果。

## 配置

当以下两个变量都存在时启用 tracing：

```sh
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

`LANGFUSE_BASE_URL` 对 CodeAgent 来说是可选项，由 Langfuse SDK 从环境变量读取。用户可以用它配置非默认区域或自托管 Langfuse。

## Trace 形状

一个代理轮次对应一个以 span 为根的 trace：

```text
react.run 或 pcode.turn
├── react.llm 或 pcode.llm generation
├── tool:read_file span
├── react.llm 或 pcode.llm generation
├── tool:grep span
└── react.llm 或 pcode.llm generation
```

无论运行成功、达到最大步数，还是遇到可恢复的请求错误，都使用同样的结构。

## 不做的事

- 不用 Langfuse 替换本地运行记录。
- 不接入 Langfuse prompt management。
- 不做 Langfuse datasets、evals、scores 或 dashboard。
- 不为了使用 Langfuse OpenAI wrapper 而迁移到 OpenAI SDK。
- 不引入协议原生 function calling。
- 不为手写 HTTP client 做 token usage 和 cost tracking。
- 不做采样、隐私 masking 或按工具关闭 trace。

## 验收标准

- AC1: 项目中存在 v3 Langfuse 可观测性的 spec、plan、checklist。
- AC2: `pyproject.toml` 声明 Langfuse Python SDK 依赖。
- AC3: 项目级 tracing 抽象在 Langfuse 未配置时返回 no-op tracer。
- AC4: 提供 tracer 时，ReAct CLI 每次用户输入对应一个运行追踪，并包含嵌套 generation 和工具 observation。
- AC5: 提供 tracer 时，PCode TUI 每次用户输入对应一个运行追踪，并包含嵌套 generation 和工具 observation。
- AC6: 单元测试可以用 fake tracer 验证 trace 边界和嵌套 observation。
- AC7: 现有运行记录测试继续通过。
- AC8: 测试不需要真实 Langfuse 凭证。

## 已确认决策

- D1: trace 边界是一次代理轮次，不是一次工具调用。
- D2: 工具调用作为子 span，因为它们只有放在用户意图和前后模型决策中才有解释力。
- D3: 本地运行记录继续作为 JSON replay/debug 的离线事实来源。
- D4: 第一版 Langfuse 集成使用手动 SDK observation，因为 CodeAgent 当前使用手写 HTTP client，而不是 OpenAI SDK。
