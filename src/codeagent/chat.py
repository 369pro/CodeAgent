from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
import json
from typing import Protocol

import httpx

from codeagent.config import ProviderConfig
from codeagent.llm import LLMError, Message


@dataclass(frozen=True)
class ChatRequest:
    system_prompt: str
    messages: list[Message]


class StreamingChatClient(Protocol):
    async def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        ...


class ProviderStreamingClient:
    def __init__(self, provider: ProviderConfig, timeout: float = 60) -> None:
        self.provider = provider
        self.timeout = timeout

    async def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        if self.provider.protocol == "openai":
            async for chunk in _stream_openai(self.provider, request, self.timeout):
                yield chunk
            return
        async for chunk in _stream_anthropic(self.provider, request, self.timeout):
            yield chunk


async def _stream_openai(provider: ProviderConfig, request: ChatRequest, timeout: float) -> AsyncIterator[str]:
    messages = [{"role": "system", "content": request.system_prompt}]
    messages.extend({"role": message.role, "content": message.content} for message in request.messages)
    payload: dict[str, object] = {
        "model": provider.model,
        "messages": messages,
        "stream": True,
        "max_tokens": provider.max_tokens,
    }
    if provider.thinking:
        payload["reasoning_effort"] = provider.reasoning_effort

    headers = {"Authorization": f"Bearer {provider.resolved_api_key}", "Content-Type": "application/json"}
    async for data in _iter_sse_data(provider, payload, headers, timeout):
        if data == "[DONE]":
            break
        for chunk in parse_openai_stream_data(data):
            yield chunk


async def _stream_anthropic(provider: ProviderConfig, request: ChatRequest, timeout: float) -> AsyncIterator[str]:
    payload: dict[str, object] = {
        "model": provider.model,
        "system": request.system_prompt,
        "messages": [message.__dict__ for message in request.messages],
        "stream": True,
        "max_tokens": provider.max_tokens,
    }
    if provider.thinking:
        payload["thinking"] = {"type": "enabled", "budget_tokens": provider.thinking_budget_tokens}

    headers = {
        "x-api-key": provider.resolved_api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    async for data in _iter_sse_data(provider, payload, headers, timeout):
        for chunk in parse_anthropic_stream_data(data):
            yield chunk


async def _iter_sse_data(
    provider: ProviderConfig,
    payload: dict[str, object],
    headers: dict[str, str],
    timeout: float,
) -> AsyncIterator[str]:
    request_timeout = httpx.Timeout(timeout, read=None)
    try:
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            async with client.stream("POST", provider.request_endpoint, json=payload, headers=headers) as response:
                if response.status_code >= 400:
                    detail = await response.aread()
                    raise LLMError(_format_http_error(provider, response.status_code, detail.decode("utf-8", "replace")))
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


def parse_openai_stream_data(data: str) -> list[str]:
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
    return chunks


def parse_anthropic_stream_data(data: str) -> list[str]:
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

    if payload.get("type") != "content_block_delta":
        return []
    delta = payload.get("delta", {})
    if not isinstance(delta, dict) or delta.get("type") != "text_delta":
        return []
    text = delta.get("text")
    return [text] if isinstance(text, str) else []


async def collect_stream(client: StreamingChatClient, request: ChatRequest) -> str:
    parts: list[str] = []
    async for chunk in client.stream(request):
        parts.append(chunk)
    return "".join(parts)


def messages_to_dicts(messages: Iterable[Message]) -> list[dict[str, str]]:
    return [message.__dict__ for message in messages]
