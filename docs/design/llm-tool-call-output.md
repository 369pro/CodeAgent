# LLM Tool Call Output Design Note

本文记录 PCode 中“模型输出里的工具调用文本”从 provider 到 Langfuse、Run Record、ReAct 解析器的流转方式。这个知识点用于排查类似 Langfuse trace 里出现 `<tool_call>`、`<| DSML | tool_calls>` 但工具没有执行的问题。

## 结论

Langfuse `pcode.llm` generation 的 `Output` 是 LLM 返回的原始 assistant 文本拼接结果，不是 CodeAgent 在项目里额外包装出的工具调用结构。

CodeAgent 当前没有向 OpenAI-compatible provider 传原生 `tools` / `tool_choice` 参数，也没有把 provider 返回值二次改写成 XML、DSML 或其他工具块格式。模型如果输出：

```text
<| DSML | tool_calls>
<| DSML | invoke name="find_file">
<| DSML | parameter name="name" string="true">llm.py</| DSML | parameter>
</| DSML | invoke>
</| DSML | tool_calls>
```

这段内容就是模型生成的 `choices[].delta.content` 文本。

## 数据流

OpenAI-compatible 流式响应进入 `chat.py` 后，只抽取文本 delta：

```text
provider SSE data
  -> parse_openai_stream_data()
  -> choices[].delta.content
  -> TextDelta(text)
```

PCode 在 `_stream_llm_output()` 中消费事件：

```text
TextDelta chunks
  -> parts.append(event.text)
  -> output = "".join(parts)
  -> generation.update(output=output)
```

因此 Langfuse UI 中 `pcode.llm` 的 `Output` 字段就是 `output`，也就是模型返回 content 的拼接值。

同一份 `output` 还会进入 ReAct 解析流程：

```text
output
  -> parse_action(output)
  -> ToolRegistry.run_prechecked(...)
  -> Observation: ...
  -> session.history
```

如果 `parse_action()` 不认识该格式，本轮会记录：

```text
Observation: Invalid response. Use either Action/Action Input or Final Answer.
```

这条 `Observation` 是 CodeAgent 追加回会话历史的反馈，不是原始 LLM 输出。

如果本轮已经成功或失败地执行过工具，后续模型输出没有工具调用、也没有 `Final Answer:` 前缀，PCode 会把这段普通文本当作最终回答。这样可以兼容模型在读完文件后直接输出 Markdown/中文总结的情况，避免终端没有展示实际总结内容。

## Hook Reminder 与工具结果的区分

`message.before_model` 类型的 Hook prompt 不以裸文本注入。PCode 会把它包装成：

```text
<hook-reminder event="message.before_model" source="shared" rule_id="...">
This is internal automation context injected by CodeAgent hooks...

...
</hook-reminder>
```

这类内容是内部自动化上下文，只影响当前 generation 的行为约束；它不是工具结果，也不是用户消息。模型被明确要求不要引用、总结、确认或把它当作用户请求来回答。

## 输入会被包装，输出不会

项目确实会包装发给模型的输入。一次请求由 Prompt Bundle 组成：

- Stable Prompt Block: 长期系统提示和工具定义。
- Environment Block: 当前工作目录、git 状态、模型等动态信息。
- System Reminders: 本轮临时提醒。
- Messages: 持久对话历史。

其中工具协议提示要求模型使用 CodeAgent 文本协议：

```text
Action: tool_name Action Input: {"key": "value"}
```

这只是对模型的指令，不是 provider 原生工具调用协议。模型仍可能因为训练偏好、provider 模板或上下文干扰输出 XML/DSML 风格文本。为了提升鲁棒性，执行层可以兼容解析这些文本格式，但可观测性里应该保留原始 `output`，方便定位模型到底生成了什么。

## 当前兼容格式

PCode 的工具调用解析入口应统一返回同一种内部结构：

```text
ParsedAction(name, tool_input, input_error)
```

当前需要兼容四类模型输出：

- CodeAgent 原生文本协议：`Action: tool_name Action Input: {"key": "value"}`
- 简化 XML 块：`<tool_call> ... </tool_call>`
- DSML 工具块：`<| DSML | tool_calls> ... </| DSML | tool_calls>`
- 工具名 XML 根标签：`<glob><pattern>**/llm.py</pattern></glob>`

兼容解析只影响是否能够执行工具，不应改变 Langfuse generation 的原始 `Output` 记录。

## DSML 全角竖线兼容

Langfuse trace 中可能看到模型输出全角竖线版本：

```text
<｜｜DSML｜｜tool_calls>
<｜｜DSML｜｜invoke name="find_file">
<｜｜DSML｜｜parameter name="name" string="true">llm.py</｜｜DSML｜｜parameter>
</｜｜DSML｜｜invoke>
</｜｜DSML｜｜tool_calls>
```

这仍然是模型生成的文本，不是 provider 原生 tool call。PCode 的 DSML fallback parser 同时兼容 ASCII `|` 和全角 `｜`，也兼容单双 pipe 与空格变体。

## 连续失败熔断

PCode 在单个 `run_turn()` 内跟踪同一个工具的连续失败次数。

规则：

- 同一个工具连续失败 3 次后，第三次 Observation 会追加 circuit breaker 提醒。
- 熔断打开后，如果模型继续调用同一个工具，PCode 返回合成工具错误，不再执行真实工具。
- 换用其他工具或某次工具成功会重置连续失败计数。

熔断错误会作为普通 Observation 回填给模型，让模型换工具或修改参数，而不是终止整个 Agent Loop。
