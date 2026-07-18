from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Literal


class ConfigError(ValueError):
    """Raised when user configuration is invalid."""


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
    max_steps: int = 18
    tool_output_limit: int = 8000
    permission_mode: str = "default"


ProtocolName = Literal["openai", "anthropic"]


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    protocol: ProtocolName
    model: str
    api_key: str
    base_url: str | None = None
    endpoint: str | None = None
    thinking: bool = False
    thinking_budget_tokens: int = 1024
    reasoning_effort: str = "medium"
    max_tokens: int = 4096

    @property
    def resolved_api_key(self) -> str:
        return self.api_key

    @property
    def request_endpoint(self) -> str:
        if self.endpoint:
            return self.endpoint
        if self.protocol == "openai":
            base = self.base_url or "https://api.openai.com/v1"
            return f"{base.rstrip('/')}/chat/completions"
        base = self.base_url or "https://api.anthropic.com"
        return f"{base.rstrip('/')}/v1/messages"


@dataclass(frozen=True)
class CodeAgentConfig:
    llm: LLMConfig
    agent: AgentConfig
    providers: tuple[ProviderConfig, ...] = ()


def load_config(path: str | Path = ".codeagent/config.yaml") -> CodeAgentConfig:
    config_path = Path(path)
    data: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = _parse_yaml(handle.read())
            if not isinstance(loaded, dict):
                raise ConfigError(f"Config at {config_path} must be a mapping.")
            data = loaded

    llm_data = data.get("llm", {})
    agent_data = data.get("agent", {})
    if not isinstance(llm_data, dict) or not isinstance(agent_data, dict):
        raise ConfigError("Config sections 'llm' and 'agent' must be mappings.")

    return CodeAgentConfig(
        llm=LLMConfig(**llm_data),
        agent=AgentConfig(**agent_data),
        providers=_load_providers(data.get("providers", [])),
    )


def _parse_yaml(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return _parse_simple_yaml(text)
    try:
        loaded = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 - surface YAML parser details cleanly.
        raise ConfigError(f"Invalid YAML: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError("Config must be a YAML mapping.")
    return loaded


def _load_providers(raw: object) -> tuple[ProviderConfig, ...]:
    if raw in (None, ""):
        return ()
    if not isinstance(raw, list):
        raise ConfigError("Config section 'providers' must be a list.")

    providers: list[ProviderConfig] = []
    for index, item in enumerate(raw):
        label = f"providers[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{label} must be a mapping.")

        name = _required_str(item, "name", label)
        protocol = _required_str(item, "protocol", label)
        if protocol not in {"openai", "anthropic"}:
            raise ConfigError(f"{label}.protocol must be 'openai' or 'anthropic'.")
        model = _required_str(item, "model", label)
        api_key = _required_str(item, "api_key", label)
        base_url = _optional_str(item, "base_url", label)
        endpoint = _optional_str(item, "endpoint", label)
        if base_url and endpoint:
            raise ConfigError(f"{label} may set either base_url or endpoint, not both.")

        provider = ProviderConfig(
            name=name,
            protocol=protocol,  # type: ignore[arg-type]
            model=model,
            api_key=_resolve_provider_key(name, api_key),
            base_url=base_url,
            endpoint=endpoint,
            thinking=_optional_bool(item, "thinking", label, False),
            thinking_budget_tokens=_optional_int(item, "thinking_budget_tokens", label, 1024),
            reasoning_effort=_optional_str(item, "reasoning_effort", label) or "medium",
            max_tokens=_optional_int(item, "max_tokens", label, 4096),
        )
        providers.append(provider)
    return tuple(providers)


def _resolve_provider_key(name: str, api_key: str) -> str:
    if not api_key.startswith("env:"):
        return api_key
    env_name = api_key[4:]
    value = os.getenv(env_name)
    if value:
        return value
    raise ConfigError(f"Provider '{name}' api_key references env:{env_name}, but it is not set.")


def _required_str(data: dict[str, object], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label}.{key} is required and must be a non-empty string.")
    return value.strip()


def _optional_str(data: dict[str, object], key: str, label: str) -> str | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ConfigError(f"{label}.{key} must be a string.")
    return value.strip()


def _optional_bool(data: dict[str, object], key: str, label: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{label}.{key} must be true or false.")
    return value


def _optional_int(data: dict[str, object], key: str, label: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{label}.{key} must be a positive integer.")
    return value


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
