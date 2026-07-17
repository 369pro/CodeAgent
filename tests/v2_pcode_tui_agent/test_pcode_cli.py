from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import asyncio

from codeagent.config import AgentConfig, CodeAgentConfig, LLMConfig, ProviderConfig
from codeagent.pcode_cli import _legacy_provider, main
from codeagent.pcode_agent import PCodeAgentSession
from codeagent.pcode_tui import PCodeApp, SNAKE_BANNER, TurnView


class PCodeCliTest(unittest.TestCase):
    def test_legacy_llm_config_becomes_openai_provider(self) -> None:
        config = CodeAgentConfig(
            llm=LLMConfig(
                provider="deepseek",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
                api_key="secret",
            ),
            agent=AgentConfig(),
        )

        provider = _legacy_provider(config)

        self.assertEqual(provider.name, "deepseek")
        self.assertEqual(provider.protocol, "openai")
        self.assertEqual(provider.request_endpoint, "https://api.deepseek.com/v1/chat/completions")

    def test_chat_mode_requires_interactive_terminal(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.yaml"
            config_path.write_text("", encoding="utf-8")

            with (
                patch("sys.argv", ["pcode", "--config", str(config_path)]),
                patch("sys.stdin.isatty", return_value=False),
                patch("sys.stderr"),
            ):
                with self.assertRaises(SystemExit) as context:
                    main()

        self.assertEqual(context.exception.code, 2)

    def test_tui_enter_submits_input(self) -> None:
        async def run() -> None:
            calls: list[str] = []

            async def fake_run_turn(self, text, turn):  # type: ignore[no-untyped-def]
                calls.append(text)
                await turn.final_delta("ok")

            provider = ProviderConfig(
                name="test",
                protocol="openai",
                model="test-model",
                api_key="secret",
            )
            app = PCodeApp((provider,), AgentConfig(), Path.cwd())

            with patch.object(PCodeAgentSession, "run_turn", new=fake_run_turn):
                async with app.run_test() as pilot:
                    await pilot.pause()
                    app.input_box.text = "hello"
                    await pilot.press("enter")
                    await pilot.pause()

            self.assertEqual(calls, ["hello"])
            self.assertEqual(app.input_box.text, "")

        asyncio.run(run())

    def test_banner_is_pcode_wordmark(self) -> None:
        self.assertIn("/\\_/\\", SNAKE_BANNER)
        self.assertIn("( o.o )", SNAKE_BANNER)
        self.assertIn("____   ____", SNAKE_BANNER)
        self.assertIn("\\____\\___/", SNAKE_BANNER)

    def test_tool_status_does_not_read_textual_internal_renderable(self) -> None:
        async def run() -> None:
            provider = ProviderConfig(
                name="test",
                protocol="openai",
                model="test-model",
                api_key="secret",
            )
            app = PCodeApp((provider,), AgentConfig(), Path.cwd())

            async with app.run_test() as pilot:
                await pilot.pause()
                turn = TurnView(app, "inspect")
                await turn.mount()
                await turn.tool_started("glob")
                await turn.tool_finished("glob", is_error=False)
                await turn.tool_finished("grep", is_error=True)

            self.assertEqual(turn.tool_lines, ["Using glob... done", "Using grep... failed"])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
