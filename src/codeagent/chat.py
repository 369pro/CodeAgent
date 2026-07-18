from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
import json
from typing import Protocol

import httpx

from codeagent.config import ProviderConfig
from codeagent.llm import LLMError, Message
from codeagent.prompts import GenerationUsage


@dataclass(frozen=True)
class ChatRequest:
    stable_prompt: str
    environment: str
    reminders: list[str]
    messages: list[Message]


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class UsageDelta:
    usage: GenerationUsage


ChatStreamEvent = TextDelta | UsageDelta


class StreamingChatClient(Protocol):
    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]: ...


class ProviderStreamingClient:
    def __init__(self, provider: ProviderConfig, timeout: float = 60) -> None:
        self.provider = provider
        self.timeout = timeout

    async def stream(self, request: ChatRequest) -> AsyncIterator[ChatStreamEvent]:
        if self.provider.protocol == "openai":
            async for chunk in _stream_openai(self.provider, request, self.timeout):
                yield chunk
            return
        async for chunk in _stream_anthropic(self.provider, request, self.timeout):
            yield chunk


async def _stream_openai(
    provider: ProviderConfig, request: ChatRequest, timeout: float
) -> AsyncIterator[ChatStreamEvent]:
    messages = [
        {"role": "system", "content": request.stable_prompt},
        {"role": "system", "content": request.environment},
    ]
    messages.extend(_messages_with_reminders(request))
    payload: dict[str, object] = {
        "model": provider.model,
        "messages": messages,
        "stream": True,
        "max_tokens": provider.max_tokens,
        # 可以记录token用量信息
        "stream_options": {"include_usage": True},
    }
    if provider.thinking:
        payload["reasoning_effort"] = provider.reasoning_effort

    headers = {
        "Authorization": f"Bearer {provider.resolved_api_key}",
        "Content-Type": "application/json",
    }
    async for data in _iter_sse_data(provider, payload, headers, timeout):
        if data == "[DONE]":
            break
        text_chunks, usage = parse_openai_stream_data(data)
        for chunk in text_chunks:
            yield TextDelta(chunk)
        if usage:
            yield UsageDelta(usage)


async def _stream_anthropic(
    provider: ProviderConfig, request: ChatRequest, timeout: float
) -> AsyncIterator[ChatStreamEvent]:
    payload: dict[str, object] = {
        "model": provider.model,
        "system": [
            {
                "type": "text",
                "text": request.stable_prompt,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": request.environment},
        ],
        "messages": _messages_with_reminders(request),
        "stream": True,
        "max_tokens": provider.max_tokens,
    }
    if provider.thinking:
        payload["thinking"] = {
            "type": "enabled",
            "budget_tokens": provider.thinking_budget_tokens,
        }

    headers = {
        "x-api-key": provider.resolved_api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    async for data in _iter_sse_data(provider, payload, headers, timeout):
        text_chunks, usage = parse_anthropic_stream_data(data)
        for chunk in text_chunks:
            yield TextDelta(chunk)
        if usage:
            yield UsageDelta(usage)


async def _iter_sse_data(
    provider: ProviderConfig,
    payload: dict[str, object],
    headers: dict[str, str],
    timeout: float,
) -> AsyncIterator[str]:
    request_timeout = httpx.Timeout(timeout, read=None)
    try:
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            async with client.stream(
                "POST", provider.request_endpoint, json=payload, headers=headers
            ) as response:
                if response.status_code >= 400:
                    detail = await response.aread()
                    raise LLMError(
                        _format_http_error(
                            provider,
                            response.status_code,
                            detail.decode("utf-8", "replace"),
                        )
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    yield line[5:].strip()
    except httpx.HTTPError as exc:
        raise LLMError(f"{provider.name} request failed: {exc}") from exc


def _format_http_error(provider: ProviderConfig, status_code: int, detail: str) -> str:
    if provider.resolved_api_key and provider.resolved_api_key in detail:
        detail = detail.replace(provider.resolved_api_key, "[redacted]")
    detail = detail.strip()
    suffix = f": {detail}" if detail else ""
    return f"{provider.name} HTTP {status_code} for model {provider.model}{suffix}"


def parse_openai_stream_data(data: str) -> tuple[list[str], GenerationUsage | None]:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Invalid OpenAI stream data: {exc}") from exc

    chunks: list[str] = []
    for choice in payload.get("choices", []):
        delta = choice.get("delta", {})
        if not isinstance(delta, dict):
            continue
        content = delta.get("content")
        if isinstance(content, str):
            chunks.append(content)
    return chunks, parse_openai_usage(payload.get("usage"))


def parse_anthropic_stream_data(data: str) -> tuple[list[str], GenerationUsage | None]:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise LLMError(f"Invalid Anthropic stream data: {exc}") from exc

    if payload.get("type") == "error":
        error = payload.get("error", {})
        if isinstance(error, dict):
            message = error.get("message") or error.get("type") or "unknown error"
        else:
            message = "unknown error"
        raise LLMError(f"Anthropic stream error: {message}")

    if payload.get("type") == "message_delta":
        delta = payload.get("delta", {})
        usage = delta.get("usage") if isinstance(delta, dict) else None
        return [], parse_anthropic_usage(usage)
    if payload.get("type") == "message_start":
        message = payload.get("message", {})
        usage = message.get("usage") if isinstance(message, dict) else None
        return [], parse_anthropic_usage(usage)
    if payload.get("type") != "content_block_delta":
        return [], None
    delta = payload.get("delta", {})
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return [], None
    text = delta.get("text")
    return ([text] if isinstance(text, str) else []), None


def parse_openai_usage(raw: object) -> GenerationUsage | None:
    if not isinstance(raw, dict):
        return None
    prompt_details = (
        raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}
    )
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    return GenerationUsage(
        input_tokens=_int_value(raw, "prompt_tokens")
        or _int_value(raw, "input_tokens"),
        output_tokens=_int_value(raw, "completion_tokens")
        or _int_value(raw, "output_tokens"),
        cache_read_tokens=_int_value(prompt_details, "cached_tokens"),
    )


def parse_anthropic_usage(raw: object) -> GenerationUsage | None:
    if not isinstance(raw, dict):
        return None
    return GenerationUsage(
        input_tokens=_int_value(raw, "input_tokens"),
        output_tokens=_int_value(raw, "output_tokens"),
        cache_write_tokens=_int_value(raw, "cache_creation_input_tokens"),
        cache_read_tokens=_int_value(raw, "cache_read_input_tokens"),
    )


def _int_value(data: dict[str, object], key: str) -> int:
    value = data.get(key)
    return value if isinstance(value, int) else 0


async def collect_stream(client: StreamingChatClient, request: ChatRequest) -> str:
    parts: list[str] = []
    async for event in client.stream(request):
        if isinstance(event, TextDelta):
            parts.append(event.text)
    return "".join(parts)


def messages_to_dicts(messages: Iterable[Message]) -> list[dict[str, str]]:
    return [message.__dict__ for message in messages]


def _messages_with_reminders(request: ChatRequest) -> list[dict[str, str]]:
    messages = [message.__dict__.copy() for message in request.messages]
    if not request.reminders:
        return messages
    reminder_text = "\n\n".join(request.reminders)
    for index in range(len(messages) - 1, -1, -1):
        if messages[index]["role"] == "user":
            messages[index]["content"] = (
                f"{reminder_text}\n\n{messages[index]['content']}"
            )
            return messages
    return [{"role": "user", "content": reminder_text}, *messages]
