from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import asyncio

from textual.widgets import Static

from codeagent.config import AgentConfig, CodeAgentConfig, LLMConfig, ProviderConfig
from codeagent.pcode_cli import _legacy_provider, main
from codeagent.pcode_agent import PCodeAgentSession, PCodeTurnResult
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
        self.assertEqual(
            provider.request_endpoint, "https://api.deepseek.com/v1/chat/completions"
        )

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

            async def fake_run_turn(self, text, turn, **kwargs):  # type: ignore[no-untyped-def]
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

    def test_tui_plan_command_is_local(self) -> None:
        async def run() -> None:
            calls: list[str] = []

            async def fake_run_turn(self, text, turn, **kwargs):  # type: ignore[no-untyped-def]
                calls.append(text)

            provider = ProviderConfig(
                name="test", protocol="openai", model="test-model", api_key="secret"
            )
            app = PCodeApp((provider,), AgentConfig(), Path.cwd())

            with patch.object(PCodeAgentSession, "run_turn", new=fake_run_turn):
                async with app.run_test() as pilot:
                    await pilot.pause()
                    app.input_box.text = "/plan"
                    await pilot.press("enter")
                    await pilot.pause()

            self.assertTrue(app.planning_mode)
            self.assertEqual(calls, [])

        asyncio.run(run())

    def test_tui_plan_command_refocuses_input(self) -> None:
        async def run() -> None:
            provider = ProviderConfig(
                name="test", protocol="openai", model="test-model", api_key="secret"
            )
            app = PCodeApp((provider,), AgentConfig(), Path.cwd())

            async with app.run_test() as pilot:
                await pilot.pause()
                app.input_box.text = "/plan"
                await pilot.press("enter")
                await pilot.pause()
                await pilot.pause()

                self.assertIs(app.focused, app.input_box)
                self.assertFalse(app.input_box.disabled)

        asyncio.run(run())

    def test_tui_status_shows_provider_and_model(self) -> None:
        async def run() -> None:
            provider = ProviderConfig(
                name="deepseek",
                protocol="openai",
                model="deepseek-v4-flash",
                api_key="secret",
            )
            app = PCodeApp((provider,), AgentConfig(), Path.cwd())

            async with app.run_test() as pilot:
                await pilot.pause()
                status = str(app.query_one("#provider-status", Static).render())
                self.assertIn("deepseek", status)
                self.assertIn("deepseek-v4-flash", status)

        asyncio.run(run())

    def test_tui_do_with_text_executes_plan(self) -> None:
        async def run() -> None:
            calls: list[tuple[str, dict[str, object]]] = []

            async def fake_run_turn(self, text, turn, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((text, kwargs))
                await turn.final_delta("ok")

            provider = ProviderConfig(
                name="test", protocol="openai", model="test-model", api_key="secret"
            )
            app = PCodeApp((provider,), AgentConfig(), Path.cwd())
            app.planning_mode = True

            with patch.object(PCodeAgentSession, "run_turn", new=fake_run_turn):
                async with app.run_test() as pilot:
                    await pilot.pause()
                    app.input_box.text = "/do implement it"
                    await pilot.press("enter")
                    await pilot.pause()

            self.assertFalse(app.planning_mode)
            self.assertEqual(calls[0][0], "implement it")
            self.assertFalse(calls[0][1]["planning_mode"])
            self.assertTrue(calls[0][1]["execute_plan"])

        asyncio.run(run())

    def test_tui_do_opens_approval_when_plan_file_exists(self) -> None:
        async def run() -> None:
            approvals: list[Path] = []

            async def fake_show_plan_approval(self, plan_path):  # type: ignore[no-untyped-def]
                approvals.append(plan_path)

            provider = ProviderConfig(
                name="test", protocol="openai", model="test-model", api_key="secret"
            )
            with TemporaryDirectory() as workspace:
                root = Path(workspace)
                plan_path = root / ".codeagent" / "plans" / "current.md"
                plan_path.parent.mkdir(parents=True)
                plan_path.write_text("# Plan\n\n1. Implement it.\n", encoding="utf-8")
                app = PCodeApp((provider,), AgentConfig(), root)
                app.planning_mode = True
                app.plan_state = app.plan_state.__class__(
                    active=True,
                    path=plan_path,
                    original_request="implement it",
                )

                with patch.object(
                    PCodeApp, "_show_plan_approval", new=fake_show_plan_approval
                ):
                    async with app.run_test() as pilot:
                        await pilot.pause()
                        app.input_box.text = "/do"
                        await pilot.press("enter")
                        await pilot.pause()

            self.assertEqual(approvals, [plan_path])
            self.assertFalse(app.planning_mode)

        asyncio.run(run())

    def test_tui_reenables_input_before_showing_plan_approval(self) -> None:
        async def run() -> None:
            async def fake_run_turn(self, text, turn, **kwargs):  # type: ignore[no-untyped-def]
                await turn.final_delta("planned")
                return PCodeTurnResult(
                    answer="planned",
                    plan_ready=True,
                    plan_path=str(kwargs["plan_path"]),
                )

            provider = ProviderConfig(
                name="test", protocol="openai", model="test-model", api_key="secret"
            )
            with TemporaryDirectory() as workspace:
                app = PCodeApp((provider,), AgentConfig(), Path(workspace))

                with patch.object(PCodeAgentSession, "run_turn", new=fake_run_turn):
                    async with app.run_test() as pilot:
                        await pilot.pause()
                        app.input_box.text = "/plan implement it"
                        await pilot.press("enter")
                        await pilot.pause()
                        await pilot.pause()

                        ready = str(app.query_one("#ready", Static).render())
                        mode = str(app.query_one("#mode-status", Static).render())
                        self.assertFalse(app.busy)
                        self.assertFalse(app.input_box.disabled)
                        self.assertTrue(app.plan_state.awaiting_approval)
                        self.assertIn("Plan ready", ready)
                        self.assertIn("plan-review", mode)

        asyncio.run(run())

    def test_tui_plan_review_feedback_revises_instead_of_executing(self) -> None:
        async def run() -> None:
            calls: list[tuple[str, dict[str, object]]] = []

            async def fake_run_turn(self, text, turn, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((text, kwargs))
                plan_path = kwargs["plan_path"]
                plan_path.write_text("# Plan\n\n1. Implement it.\n", encoding="utf-8")
                if len(calls) == 1:
                    await turn.final_delta("planned")
                    return PCodeTurnResult(
                        answer="planned",
                        plan_ready=True,
                        plan_path=str(plan_path),
                    )
                await turn.final_delta("revised")
                return PCodeTurnResult(answer="revised")

            provider = ProviderConfig(
                name="test", protocol="openai", model="test-model", api_key="secret"
            )
            with TemporaryDirectory() as workspace:
                app = PCodeApp((provider,), AgentConfig(), Path(workspace))

                with patch.object(PCodeAgentSession, "run_turn", new=fake_run_turn):
                    async with app.run_test() as pilot:
                        await pilot.pause()
                        app.input_box.text = "/plan implement it"
                        await pilot.press("enter")
                        await pilot.pause()
                        await pilot.pause()

                        app.input_box.text = "需要用 cpp 实现"
                        await pilot.press("enter")
                        await pilot.pause()

                self.assertEqual(calls[1][0], "需要用 cpp 实现")
                self.assertTrue(calls[1][1]["planning_mode"])
                self.assertFalse(calls[1][1]["execute_plan"])

        asyncio.run(run())

    def test_tui_plan_review_do_executes_approved_plan(self) -> None:
        async def run() -> None:
            calls: list[tuple[str, dict[str, object]]] = []

            async def fake_run_turn(self, text, turn, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((text, kwargs))
                plan_path = kwargs["plan_path"]
                if len(calls) == 1:
                    plan_path.write_text("# Plan\n\n1. Implement it.\n", encoding="utf-8")
                    await turn.final_delta("planned")
                    return PCodeTurnResult(
                        answer="planned",
                        plan_ready=True,
                        plan_path=str(plan_path),
                    )
                await turn.final_delta("done")
                return PCodeTurnResult(answer="done")

            provider = ProviderConfig(
                name="test", protocol="openai", model="test-model", api_key="secret"
            )
            with TemporaryDirectory() as workspace:
                app = PCodeApp((provider,), AgentConfig(), Path(workspace))

                with patch.object(PCodeAgentSession, "run_turn", new=fake_run_turn):
                    async with app.run_test() as pilot:
                        await pilot.pause()
                        app.input_box.text = "/plan implement it"
                        await pilot.press("enter")
                        await pilot.pause()
                        await pilot.pause()

                        app.input_box.text = "/do"
                        await pilot.press("enter")
                        await pilot.pause()

                self.assertEqual(calls[1][0], "Execute approved plan")
                self.assertFalse(calls[1][1]["planning_mode"])
                self.assertTrue(calls[1][1]["execute_plan"])
                self.assertIn("Implement it", calls[1][1]["approved_plan"])

        asyncio.run(run())

    def test_tui_permission_prompt_during_approved_plan_runs_in_worker(self) -> None:
        async def run() -> None:
            approvals: list[object] = []

            async def fake_run_turn(self, text, turn, **kwargs):  # type: ignore[no-untyped-def]
                approvals.append(
                    await turn.permission_requested("write_file", "write README.md")
                )
                await turn.final_delta("permission handled")
                return PCodeTurnResult(answer="permission handled")

            provider = ProviderConfig(
                name="test", protocol="openai", model="test-model", api_key="secret"
            )
            with TemporaryDirectory() as workspace:
                root = Path(workspace)
                plan_path = root / ".codeagent" / "plans" / "current.md"
                plan_path.parent.mkdir(parents=True)
                plan_path.write_text("# Plan\n\n1. Write README.\n", encoding="utf-8")
                app = PCodeApp((provider,), AgentConfig(), root)
                app.plan_state = app.plan_state.__class__(
                    active=True,
                    awaiting_approval=True,
                    path=plan_path,
                    original_request="write README",
                )

                with patch.object(PCodeAgentSession, "run_turn", new=fake_run_turn):
                    async with app.run_test() as pilot:
                        await pilot.pause()
                        app.input_box.text = "/do"
                        await pilot.press("enter")
                        await pilot.pause()
                        await pilot.press("down", "down", "down", "enter")
                        await pilot.pause()
                        await pilot.pause()

                self.assertEqual(approvals, ["deny"])
                self.assertFalse(app.busy)
                self.assertFalse(app.input_box.disabled)

        asyncio.run(run())

    def test_tui_plan_with_text_starts_planning_turn(self) -> None:
        async def run() -> None:
            calls: list[tuple[str, dict[str, object]]] = []

            async def fake_run_turn(self, text, turn, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((text, kwargs))
                await turn.final_delta("ok")

            provider = ProviderConfig(
                name="test", protocol="openai", model="test-model", api_key="secret"
            )
            with TemporaryDirectory() as workspace:
                app = PCodeApp((provider,), AgentConfig(), Path(workspace))

                with patch.object(PCodeAgentSession, "run_turn", new=fake_run_turn):
                    async with app.run_test() as pilot:
                        await pilot.pause()
                        app.input_box.text = "/plan implement it"
                        await pilot.press("enter")
                        await pilot.pause()

                self.assertTrue(app.planning_mode)
                self.assertEqual(calls[0][0], "implement it")
                self.assertTrue(calls[0][1]["planning_mode"])
                self.assertEqual(
                    calls[0][1]["plan_path"],
                    Path(workspace) / ".codeagent" / "plans" / "current.md",
                )

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

            self.assertEqual(
                turn.tool_lines, ["Using glob... done", "Using grep... failed"]
            )

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
