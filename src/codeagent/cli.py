from __future__ import annotations

import argparse
from pathlib import Path

from codeagent.agent import ReActAgent
from codeagent.config import load_config
from codeagent.llm import DeepSeekChatClient, LLMError
from codeagent.tools import build_default_registry


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the minimal CodeAgent ReAct loop.")
    parser.add_argument("prompt", nargs="?", help="User request for the agent. Omit it for interactive mode.")
    parser.add_argument("--config", default=".codeagent/config.yaml", help="Path to config YAML.")
    parser.add_argument("--workspace", default=".", help="Workspace root for tools.")
    args = parser.parse_args()

    config = load_config(args.config)
    llm = DeepSeekChatClient(config.llm)
    tools = build_default_registry(Path(args.workspace))
    agent = ReActAgent(llm, tools, config.agent)
    if args.prompt:
        _run_once(agent, args.prompt)
        return

    print("CodeAgent interactive CLI. Type 'exit' or press Ctrl+C to quit.")
    try:
        while True:
            prompt = input("\ncodeagent> ").strip()
            if prompt.lower() in {"exit", "quit", "q"}:
                break
            if not prompt:
                continue
            _run_once(agent, prompt)
    except KeyboardInterrupt:
        print("\nBye.")
        return


def _run_once(agent: ReActAgent, prompt: str) -> None:
    try:
        result = agent.run(prompt)
    except LLMError as exc:
        print(f"LLM error: {exc}")
        return
    except RuntimeError as exc:
        print(f"Agent error: {exc}")
        return
    print(result.answer)


if __name__ == "__main__":
    main()
