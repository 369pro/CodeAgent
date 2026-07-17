from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-v4-flash"
    api_key: str | None = None
    api_key_env: str | None = "DEEPSEEK_API_KEY"
    temperature: float = 0

    @property
    def resolved_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            value = os.getenv(self.api_key_env)
            if value:
                return value
        raise ValueError("LLM API key is missing. Set llm.api_key or llm.api_key_env.")


@dataclass(frozen=True)
class AgentConfig:
    max_steps: int = 10
    tool_output_limit: int = 8000


@dataclass(frozen=True)
class CodeAgentConfig:
    llm: LLMConfig
    agent: AgentConfig


def load_config(path: str | Path = ".codeagent/config.yaml") -> CodeAgentConfig:
    config_path = Path(path)
    data: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = _parse_simple_yaml(handle.read())
            if not isinstance(loaded, dict):
                raise ValueError(f"Config at {config_path} must be a mapping.")
            data = loaded

    llm_data = data.get("llm", {})
    agent_data = data.get("agent", {})
    if not isinstance(llm_data, dict) or not isinstance(agent_data, dict):
        raise ValueError("Config sections 'llm' and 'agent' must be mappings.")

    return CodeAgentConfig(
        llm=LLMConfig(**llm_data),
        agent=AgentConfig(**agent_data),
    )


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the tiny YAML subset used by .codeagent/config.yaml."""
    root: dict[str, Any] = {}
    current_section: dict[str, Any] | None = None
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith(" "):
            key, value = _split_yaml_pair(line)
            if value == "":
                section: dict[str, Any] = {}
                root[key] = section
                current_section = section
            else:
                root[key] = _parse_scalar(value)
                current_section = None
            continue
        if current_section is None:
            raise ValueError(f"Nested value without a section: {raw_line}")
        key, value = _split_yaml_pair(line.strip())
        current_section[key] = _parse_scalar(value)
    return root


def _split_yaml_pair(line: str) -> tuple[str, str]:
    if ":" not in line:
        raise ValueError(f"Invalid YAML line: {line}")
    key, value = line.split(":", 1)
    key = key.strip()
    if not key:
        raise ValueError(f"Invalid YAML key: {line}")
    return key, value.strip()


def _parse_scalar(value: str) -> object:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value
