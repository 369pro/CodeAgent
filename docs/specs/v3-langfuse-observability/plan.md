# v3 Langfuse 可观测性计划

## 步骤

1. 补充领域语言和 v3 规格文档。
2. 增加 `langfuse` 运行时依赖。
3. 新增 `codeagent.observability`：
   - `Tracer` protocol
   - no-op tracer
   - 基于环境变量创建的 Langfuse tracer
4. 接入 `ReActAgent.run`：
   - 每次用户输入一个根 span
   - 每次 `llm.complete` 一个 generation observation
   - 每次 `ToolRegistry.run` 一个工具 span
   - 每次运行结束后 flush
5. 接入 `PCodeAgentSession.run_turn`：
   - 每次用户输入一个根 span
   - 每次 streaming 模型调用一个 generation observation
   - 每次 `ToolRegistry.run` 一个工具 span
   - 每个 turn 结束后 flush
6. 增加 fake tracer 测试，覆盖 trace 形状和 no-op 行为。
7. 先跑聚焦测试，再跑完整测试套件。

## 实现备注

- Langfuse 在运行时保持可选。缺少凭证或 import 失败时，tracing 自动降级为 no-op。
- 不把 provider API key、环境变量值或完整 config 对象写入 metadata。
- 保持当前本地 `RunRecorder` schema 不变。
- 测试优先通过构造函数注入 tracer，避免接触真实 Langfuse。
