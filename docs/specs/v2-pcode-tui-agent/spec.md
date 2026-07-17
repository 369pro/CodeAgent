# v2 PCode TUI Agent

## Goal

Add a parallel `pcode` terminal chat entrypoint that runs a streaming LLM-backed agent with the existing workspace tools. The existing `codeagent` ReAct CLI remains available for compatibility.

This release prioritizes the provider protocol, ReAct/tool loop, streaming feedback, and recoverable errors. The terminal UI only needs a clear, functional layout; visual polish is not the main work.

## Entrypoints

- `pcode`: starts the new TUI agent.
- `pcode chat`: explicit alias for the new TUI agent.
- `pcode agent [prompt]`: runs the existing ReAct CLI behavior.
- `run.sh` invokes the same entrypoint, so `./run.sh` starts the TUI and `./run.sh agent` starts the old ReAct mode.
- The existing `codeagent` command remains compatible with the old ReAct CLI.

## Configuration

The TUI reuses `.codeagent/config.yaml` and reads a top-level `providers` list. Existing `llm` and `agent` sections remain for the old CLI and shared agent limits.

Example:

```yaml
providers:
  - name: Claude
    protocol: anthropic
    model: claude-sonnet-4-20250514
    api_key: env:ANTHROPIC_API_KEY
    base_url: https://api.anthropic.com
    max_tokens: 4096
    thinking: true
    thinking_budget_tokens: 1024

  - name: OpenAI
    protocol: openai
    model: gpt-5
    api_key: env:OPENAI_API_KEY
    base_url: https://api.openai.com/v1
    max_tokens: 4096
    thinking: true
    reasoning_effort: medium
```

Rules:

- Required provider fields: `name`, `protocol`, `model`, `api_key`.
- `protocol` is `openai` or `anthropic`.
- `api_key` may be an inline secret or `env:VAR_NAME`; the variable value is resolved at startup and never displayed.
- `base_url` means protocol root URL; `endpoint` means full request URL. They are optional, but at most one may be set.
- Defaults:
  - OpenAI endpoint: `https://api.openai.com/v1/chat/completions`
  - Anthropic endpoint: `https://api.anthropic.com/v1/messages`
  - `thinking: false`
  - `thinking_budget_tokens: 1024`
  - `reasoning_effort: medium`
  - `max_tokens: 4096`
- TUI startup validates every configured provider. Any invalid provider causes a readable startup error and exit before entering the TUI.
- If one provider is configured, it is selected automatically. If multiple are configured, the TUI shows a keyboard-selectable provider list in YAML order.

## Provider Protocols

OpenAI protocol:

- Uses OpenAI-compatible Chat Completions streaming.
- Request path is `/chat/completions` when `base_url` is used.
- Sends `stream: true`, `model`, `messages`, and `max_tokens`.
- `system_prompt` is prepended as a `system` message.
- Text deltas come from `choices[].delta.content`.
- Known reasoning/thinking delta fields are discarded.
- `[DONE]` ends the stream.
- Responses API is out of scope for this release.

Anthropic protocol:

- Uses Messages streaming.
- Request path is `/v1/messages` when `base_url` is used.
- Sends `stream: true`, `model`, `system`, `messages`, and `max_tokens`.
- Uses `x-api-key` and `anthropic-version` headers.
- Text deltas come from `content_block_delta` events where `delta.type == "text_delta"`.
- Thinking deltas and signatures are discarded.
- `message_stop` ends the stream.
- `error` events become recoverable request errors.

## Tool Integration

The TUI integrates existing tools through the current ReAct text protocol, not protocol-native function calling.

Rules:

- Default tools are enabled: `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `bash`, `git_status`, and `git_diff`.
- Workspace is `--workspace` or the current directory.
- Tool output is truncated using the existing agent config limit.
- No permission confirmation system is added in this release.
- The model may emit `Action` / `Action Input`; the app executes the tool and feeds `Observation` back to the model.
- The UI shows concise tool status, such as `Using grep... done`, but does not show raw observations by default.
- Only `Final Answer` content is displayed as assistant text.
- Intermediate `Thought` content is not rendered.

Model context keeps the full ReAct trajectory for successful and partially failed turns so the next turn can refer to previous file reads or command output. UI history shows only user messages, tool status summaries, final answers, and errors.

## TUI Behavior

- Banner: snake/Python-like ASCII art, app name, version, and current workspace.
- Ready line replaces any MCP/tool status copied from earlier references.
- Conversation area shows messages in order.
- Bottom input has a border, `>` prompt, and `Send a message...` placeholder.
- Status bar shows provider name on the left and model on the right.
- `/exit` and `Ctrl+C` exit safely.
- During a request, input is disabled.
- `Enter` submits, `Alt+Enter` and `Ctrl+J` insert a newline.
- Current assistant header shows elapsed time from request start, e.g. `Imagining... (5s)`.
- Tool loop phases show concise statuses.
- Final answer streams as plain text once `Final Answer:` is detected, then is rendered as Markdown in place.
- Errors are shown in a distinct style and do not exit the session.

## Out of Scope

- Protocol-native tool/function calling.
- MCP integration.
- Runtime provider/model switching.
- Slash command system beyond `/exit`.
- Long-term memory or persisted chat history.
- Context summarization or truncation.
- Streaming cancellation.
- Automatic retry/backoff.
- Token or cost accounting.
- Multimodal input.
