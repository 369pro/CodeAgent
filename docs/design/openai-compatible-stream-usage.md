# OpenAI-Compatible Stream Usage Design

本文说明 PCode 调用 DeepSeek 这类 OpenAI-compatible provider 时，流式文本和 token usage 是如何从同一条 SSE 响应链路进入 CodeAgent、Run Record 和 Langfuse 的。

## 当前 DeepSeek 协议

PCode 使用 `ProviderStreamingClient` 调用模型。它根据 `ProviderConfig.protocol` 分流：

- `openai`: 走 `/chat/completions` 请求格式。
- `anthropic`: 走 `/v1/messages` 请求格式。

当前 `.codeagent/config.yaml` 只有 legacy `llm` 配置，没有顶层 `providers` 列表。PCode 启动时会通过 `_legacy_provider()` 把 legacy 配置转换为 provider，并固定使用：

```python
protocol="openai"
```

因此 DeepSeek 当前走的是 OpenAI-compatible 模式，请求 endpoint 是：

```text
https://api.deepseek.com/v1/chat/completions
```

## 事件流抽象

Provider streaming 输出不是简单的字符串流，而是统一成事件流：

```python
ChatStreamEvent = TextDelta | UsageDelta
```

两种事件表示同一次模型调用里的不同结果：

- `TextDelta`: 模型生成文本的增量片段，用于拼接最终 assistant output。
- `UsageDelta`: provider 返回的 token usage，用于本地 Run Record 和 Langfuse metadata。

这样上层只需要消费一个 async stream，不需要为 usage 额外发请求。

## 请求 Usage

OpenAI-compatible 流式请求会带上：

```python
"stream": True,
"stream_options": {"include_usage": True},
```

其中 `stream_options.include_usage` 表示希望 provider 在流式响应末尾额外返回 usage chunk。不是所有 OpenAI-compatible 网关都支持该字段；如果 provider 不返回 usage，CodeAgent 就不会产生 `UsageDelta`。

## SSE 原始形态

一次请求的 SSE 响应可能类似这样：

```text
data: {"choices":[{"delta":{"content":"Final"}}],"usage":null}

data: {"choices":[{"delta":{"content":" Answer"}}],"usage":null}

data: {"choices":[{"delta":{"content":": done"}}],"usage":null}

data: {"choices":[],"usage":{"prompt_tokens":3270,"completion_tokens":103,"prompt_tokens_details":{"cached_tokens":2304}}}

data: [DONE]
```

前几行是文本 chunk，最后一行 JSON 是 usage chunk，`[DONE]` 只是流结束标记。

## 解析和 Yield

`_stream_openai()` 对每个 SSE `data` 调用：

```python
text_chunks, usage = parse_openai_stream_data(data)
```

解析结果可能是：

```python
(["Final"], None)
([" Answer"], None)
([": done"], None)
([], GenerationUsage(input_tokens=3270, output_tokens=103, cache_read_tokens=2304))
```

然后 `_stream_openai()` 把解析结果转换为事件：

```python
for chunk in text_chunks:
    yield TextDelta(chunk)
if usage:
    yield UsageDelta(usage)
```

所以同一次 `client.stream(request)` 对外看到的是：

```python
TextDelta("Final")
TextDelta(" Answer")
TextDelta(": done")
UsageDelta(
    GenerationUsage(
        input_tokens=3270,
        output_tokens=103,
        cache_read_tokens=2304,
    )
)
```

这就是接收方需要判断两种类型的原因：同一个异步迭代器里既有文本事件，也有用量事件。

## 接收方处理

PCode 在 `_stream_llm_output()` 中消费事件：

```python
parts = []
usage = None

async for event in self.client.stream(request):
    if isinstance(event, TextDelta):
        parts.append(event.text)
        continue
    if isinstance(event, UsageDelta):
        usage = event.usage

output = "".join(parts)
```

文本事件会拼成最终模型输出。usage 事件会被暂存到 `usage` 变量。

如果收到了 usage，PCode 会把它写入 Langfuse generation metadata：

```python
metadata["usage"] = usage.__dict__
generation.update(output=output, metadata=metadata)
```

也会写入本地 run record 的 `generation_usage` 字段。

## Usage 字段归一化

Langfuse UI 中看到的：

```yaml
usage:
  input_tokens: 3270
  output_tokens: 103
  cache_write_tokens: 0
  cache_read_tokens: 2304
```

不是模型文本输出中的字段，也不一定是 provider 原始字段名，而是 CodeAgent 的 `GenerationUsage` 结构。

OpenAI-compatible usage 会被归一化为：

- `prompt_tokens` 或 `input_tokens` -> `input_tokens`
- `completion_tokens` 或 `output_tokens` -> `output_tokens`
- `prompt_tokens_details.cached_tokens` 或 `input_tokens_details.cached_tokens` -> `cache_read_tokens`
- OpenAI-compatible 当前没有解析 cache write 字段，默认为 `0`

Anthropic usage 则走另一套解析逻辑：

- `input_tokens` -> `input_tokens`
- `output_tokens` -> `output_tokens`
- `cache_creation_input_tokens` -> `cache_write_tokens`
- `cache_read_input_tokens` -> `cache_read_tokens`

## 排查要点

如果 Langfuse 或本地 Run Record 中没有 usage，优先检查：

1. 当前 provider 是否确实走 `protocol="openai"` 或 `protocol="anthropic"`。
2. OpenAI-compatible 请求是否带了 `stream_options.include_usage`。
3. provider 的 SSE 响应末尾是否真的返回了 `usage` JSON。
4. `parse_openai_usage()` 或 `parse_anthropic_usage()` 是否覆盖了 provider 实际字段名。

模型的 assistant 文本中没有 usage 是正常现象。usage 属于 provider 响应元数据，和文本 delta 共享同一条 stream，但不是文本内容的一部分。
