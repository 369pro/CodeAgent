# Prompt 设计面试题

## 题目

你在 CodeAgent 里是如何设计 prompt 组装的？为什么不直接维护一个大 system prompt 字符串？

## 推荐回答

我把 prompt 组装拆成两层：稳定的 Prompt Section 装配层，以及每次请求动态生成的 Prompt Bundle。Section/Builder 这一层直接参考 MewCode 的实现方式，只做当前项目适配。

第一层是 `PromptSection + PromptBuilder`。每个 Prompt Section 有 `name`、`priority`、`content` 三个字段，分别代表职责、排序和文本内容。固定 section 采用 MewCode 风格的命名：`Identity`、`System`、`DoingTasks`、`ExecutingActions`、`UsingTools`、`ToneStyle`、`TextOutput`，CodeAgent 额外追加 `ToolDefinitions`。`PromptBuilder` 负责按 priority 排序、过滤空内容、用空行拼接。这样做的好处是新增一类指令只要新增 section，不需要去改一个巨大字符串。

第二层是 Prompt Bundle。一次模型请求不是只有 stable prompt，还包括动态环境信息、临时 system reminder 和历史消息：

- `stable_prompt`: 稳定系统提示和工具定义，跨轮保持逐字节一致，用于 provider 缓存。
- `environment`: 工作目录、平台、当前日期、git 状态、应用版本、当前模型，每轮动态生成，不进缓存。
- `reminders`: 运行时补充提醒，例如规划模式提醒，用 `<system-reminder>` 包裹，不写入持久历史。
- `messages`: 会话历史。

Provider 适配层再把 Prompt Bundle 翻译成具体请求。Anthropic 使用 system content blocks，在稳定块末尾加 cache breakpoint；OpenAI-compatible provider 保持稳定块在请求前缀，并尽量解析 cached token 字段。

这个设计解决了三个问题：

1. **可维护性**：提示词按职责拆分，不再是难以修改的大字符串。
2. **缓存确定性**：稳定块不混入日期、git 状态、历史消息等变化内容。
3. **运行时注入**：规划模式等动态提醒可以进入当前请求，但不污染持久历史。

## 追问 1：为什么工具定义也放进 stable prompt？

工具定义通常跨轮不变，而且 token 较多，适合进入缓存前缀。为了保证缓存命中，工具按名称排序，参数 schema 用 canonical JSON 渲染，避免 dict 顺序或空格变化导致缓存失效。

## 追问 2：为什么 environment 不放进 stable prompt？

environment 包含日期、工作区状态、git dirty 数量、当前模型等动态信息。如果放进 stable prompt，会导致稳定前缀每轮变化，provider 缓存基本无法命中。所以它必须作为单独动态块放在稳定块之后。

## 追问 3：system reminder 为什么不写入 history？

system reminder 是本轮控制信息，例如“规划模式只能用只读工具”。它不是用户真实输入，也不应该影响后续会话恢复。写入 history 会污染对话语义，还可能破坏 provider 的消息角色配对。

## 追问 4：规划模式如何保证模型真的不写文件？

用了双层约束。第一层是 prompt 层，规划模式下只把只读工具定义暴露给模型。第二层是执行层，运行时 registry 也切到只读工具面，即使模型输出 `write_file`，也会得到 unknown tool/error，不会真的修改文件。

## 追问 5：这个设计的 trade-off 是什么？

代价是请求模型从一个字符串变成了结构化对象，provider 适配层更复杂。但收益更大：缓存策略可验证、提示词可维护、动态提醒不会污染历史，而且 OpenAI-compatible 和 Anthropic-compatible 可以共享同一套上层语义。
