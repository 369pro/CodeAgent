# v3 Langfuse 可观测性检查清单

- [x] `docs/specs/v3-langfuse-observability/` 下存在 spec、plan、checklist。
- [x] `CONTEXT.md` 记录已确认的可观测性语言。
- [x] 运行时依赖包含 Langfuse Python SDK v4。
- [x] 缺少 Langfuse 环境变量时返回 no-op tracer。
- [x] ReAct 每次用户输入创建一个根 trace。
- [x] ReAct 模型调用创建 generation observation。
- [x] ReAct 工具调用创建子工具 span。
- [x] PCode 每次用户输入创建一个根 trace。
- [x] PCode streaming 模型调用创建 generation observation。
- [x] PCode 工具调用创建子工具 span。
- [x] 成功和失败运行结束后都会 flush tracing。
- [x] fake tracer 测试覆盖 trace 边界，且不依赖网络。
- [x] 现有运行记录行为保持不变。
- [x] 聚焦测试通过。
- [x] 完整测试套件通过。
