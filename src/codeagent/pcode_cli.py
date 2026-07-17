from __future__ import annotations

import argparse
from pathlib import Path
import sys

from codeagent.agent import ReActAgent
from codeagent.cli import _run_once
from codeagent.config import CodeAgentConfig, ConfigError, ProviderConfig, load_config
from codeagent.llm import DeepSeekChatClient
from codeagent.tools import build_default_registry


def main() -> None:
    parser = argparse.ArgumentParser(description="Run PCode.")
    parser.add_argument("mode", nargs="?", default="chat", choices=["chat", "agent"], help="Mode to run.")
    parser.add_argument("prompt", nargs="?", help="Prompt for agent mode.")
    parser.add_argument("--config", default=".codeagent/config.yaml", help="Path to config YAML.")
    parser.add_argument("--workspace", default=".", help="Workspace root for tools.")
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    workspace = Path(args.workspace).resolve()
    if args.mode == "agent":
        _run_agent_mode(config, workspace, args.prompt)
        return

    if not sys.stdin.isatty():
        print("PCode chat mode requires an interactive terminal. Use './run.sh agent \"<prompt>\"' for non-interactive runs.", file=sys.stderr)
        raise SystemExit(2)

    try:
        providers = config.providers or (_legacy_provider(config),)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    from codeagent.pcode_tui import PCodeApp

    PCodeApp(providers, config.agent, workspace).run()


def _legacy_provider(config: CodeAgentConfig) -> ProviderConfig:
    try:
        api_key = config.llm.resolved_api_key
    except ValueError as exc:
        raise ConfigError(
            "providers is empty and legacy llm.api_key/llm.api_key_env could not be resolved."
        ) from exc
    return ProviderConfig(
        name=config.llm.provider,
        protocol="openai",
        model=config.llm.model,
        api_key=api_key,
        base_url=config.llm.base_url,
    )


def _run_agent_mode(config, workspace: Path, prompt: str | None) -> None:  # type: ignore[no-untyped-def]
    llm = DeepSeekChatClient(config.llm)
    tools = build_default_registry(workspace, output_limit=config.agent.tool_output_limit)
    agent = ReActAgent(llm, tools, config.agent)
    if prompt:
        _run_once(agent, prompt)
        return

    print("CodeAgent interactive CLI. Type 'exit' or press Ctrl+C to quit.")
    try:
        while True:
            user_input = input("\ncodeagent> ").strip()
            if user_input.lower() in {"exit", "quit", "q"}:
                break
            if user_input:
                _run_once(agent, user_input)
    except KeyboardInterrupt:
        print("\nBye.")


if __name__ == "__main__":
    main()
