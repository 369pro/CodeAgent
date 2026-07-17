from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib import request

from codeagent.config import LLMConfig


@dataclass(frozen=True)
class Message:
    role: str
    content: str


class ChatClient(Protocol):
    def complete(self, messages: list[Message]) -> str:
        ...


class LLMError(RuntimeError):
    """Raised when the configured LLM provider cannot complete a request."""


class DeepSeekChatClient:
    """Tiny OpenAI-compatible chat client for DeepSeek."""

    def __init__(self, config: LLMConfig, timeout: float = 60) -> None:
        self.config = config
        self.timeout = timeout

    def complete(self, messages: list[Message]) -> str:
        payload = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "messages": [message.__dict__ for message in messages],
        }
        body = json.dumps(payload).encode("utf-8")
        api_request = request.Request(
            f"{self.config.base_url.rstrip('/')}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.config.resolved_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(api_request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise LLMError(f"DeepSeek HTTP {exc.code}: {detail or exc.reason}") from exc
        except URLError as exc:
            raise LLMError(f"DeepSeek request failed: {exc.reason}") from exc
        return data["choices"][0]["message"]["content"]
