from __future__ import annotations

from collections.abc import AsyncIterator
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

from codeagent.chat import ChatRequest, TextDelta
from codeagent.config import AgentConfig
from codeagent.hooks import HookEventContext, HookManager
from codeagent.pcode_agent import PCodeAgentSession
from codeagent.permissions import PermissionChecker
from codeagent.tools import build_default_registry


class FakeStreamingClient:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.requests: list[ChatRequest] = []

    async def stream(self, request: ChatRequest) -> AsyncIterator[TextDelta]:
        self.requests.append(request)
        yield TextDelta(self.outputs.pop(0))


class FakeEvents:
    def __init__(self) -> None:
        self.tools: list[str] = []
        self.final = ""
        self.permission_requests: list[str] = []

    async def tool_started(self, name: str) -> None:
        self.tools.append(f"{name}:started")

    async def tool_finished(self, name: str, is_error: bool) -> None:
        self.tools.append(f"{name}:{'failed' if is_error else 'done'}")

    async def final_delta(self, text: str) -> None:
        self.final += text

    async def permission_requested(self, tool_name: str, description: str) -> str:
        self.permission_requests.append(f"{tool_name}: {description}")
        return "deny"


class HookManagerTest(unittest.TestCase):
    def test_shared_hooks_cannot_execute_command_or_http(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            hooks_path = root / ".codeagent" / "hooks.yaml"
            hooks_path.parent.mkdir()
            hooks_path.write_text(
                yaml.safe_dump(
                    {
                        "hooks": [
                            {
                                "event": "turn.end",
                                "action": {"type": "command", "argv": ["echo", "x"]},
                            },
                            {
                                "event": "turn.end",
                                "action": {
                                    "type": "http",
                                    "method": "POST",
                                    "url": "https://example.com",
                                },
                            },
                            {
                                "event": "message.before_model",
                                "action": {"type": "prompt", "prompt": "ok"},
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            manager = HookManager.load_for_workspace(root, include_user_hooks=False)

            self.assertEqual(len(manager.rules), 1)
            self.assertTrue(any("disabled in shared hooks" in warning.message for warning in manager.warnings))

    def test_command_requires_argv_and_https_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            hooks_path = root / ".codeagent" / "hooks.local.yaml"
            hooks_path.parent.mkdir()
            hooks_path.write_text(
                yaml.safe_dump(
                    {
                        "hooks": [
                            {
                                "event": "turn.end",
                                "action": {"type": "command", "command": "echo x"},
                            },
                            {
                                "event": "turn.end",
                                "action": {
                                    "type": "http",
                                    "method": "POST",
                                    "url": "http://example.com",
                                },
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )

            manager = HookManager.load_for_workspace(root, include_user_hooks=False)

            self.assertFalse(manager.rules)
            self.assertTrue(any("use argv" in warning.message for warning in manager.warnings))
            self.assertTrue(any("https://" in warning.message for warning in manager.warnings))

    def test_background_is_only_allowed_on_turn_end(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            hooks_path = root / ".codeagent" / "hooks.local.yaml"
            hooks_path.parent.mkdir()
            hooks_path.write_text(
                yaml.safe_dump(
                    {
                        "hooks": [
                            {
                                "event": "tool.after",
                                "action": {
                                    "type": "command",
                                    "argv": ["echo", "x"],
                                    "background": True,
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            manager = HookManager.load_for_workspace(root, include_user_hooks=False)

            self.assertFalse(manager.rules)
            self.assertTrue(any("background is only allowed" in warning.message for warning in manager.warnings))

    def test_command_receives_minimal_event_context_on_stdin(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                hook_file = root / ".codeagent" / "hooks.local.yaml"
                hook_file.parent.mkdir()
                hook_file.write_text(
                    yaml.safe_dump(
                        {
                            "hooks": [
                                {
                                    "event": "turn.end",
                                    "action": {
                                        "type": "command",
                                        "argv": [
                                            sys.executable,
                                            "-c",
                                            "import json,sys; d=json.load(sys.stdin); print(sorted(d)); print(d['event'])",
                                        ],
                                    },
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                manager = HookManager.load_for_workspace(root, include_user_hooks=False)

                results = await manager.fire(HookEventContext("turn.end", root))

                self.assertEqual(results[0].status, "ok")
                self.assertIn("turn.end", results[0].output)
                self.assertNotIn("history", results[0].output)

        asyncio.run(run())


class PCodeHookIntegrationTest(unittest.TestCase):
    def test_tool_before_prompt_blocks_before_permission_request(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                hooks_path = root / ".codeagent" / "hooks.yaml"
                hooks_path.parent.mkdir()
                hooks_path.write_text(
                    yaml.safe_dump(
                        {
                            "hooks": [
                                {
                                    "event": "tool.before",
                                    "if": {"rule": "bash(git push *)"},
                                    "action": {
                                        "type": "prompt",
                                        "prompt": "Do not mutate remotes from hooks test.",
                                    },
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                client = FakeStreamingClient(
                    [
                        'Thought: push\nAction: bash\nAction Input: {"command":"git push origin main"}',
                        "Final Answer: I will not push.",
                    ]
                )
                events = FakeEvents()
                checker = PermissionChecker.for_workspace(root)
                session = PCodeAgentSession(
                    client,
                    build_default_registry(root, permission_checker=checker),
                    AgentConfig(max_steps=3),
                    hook_manager=HookManager.load_for_workspace(root, include_user_hooks=False),
                )

                result = await session.run_turn("push", events)

                self.assertEqual(result.answer, "I will not push.")
                self.assertEqual(events.tools, ["bash:started", "bash:failed"])
                self.assertFalse(events.permission_requests)
                self.assertIn("Hook blocked tool call", session.history[-2].content)
                self.assertIn("Do not mutate remotes", session.history[-2].content)

        asyncio.run(run())

    def test_message_before_model_prompt_is_added_as_reminder(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                hooks_path = root / ".codeagent" / "hooks.yaml"
                hooks_path.parent.mkdir()
                hooks_path.write_text(
                    yaml.safe_dump(
                        {
                            "hooks": [
                                {
                                    "event": "message.before_model",
                                    "action": {
                                        "type": "prompt",
                                        "prompt": "Remember the hook reminder.",
                                    },
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                client = FakeStreamingClient(["Final Answer: done"])
                session = PCodeAgentSession(
                    client,
                    build_default_registry(root),
                    AgentConfig(max_steps=1),
                    hook_manager=HookManager.load_for_workspace(root, include_user_hooks=False),
                )

                await session.run_turn("answer", FakeEvents())

                reminder = client.requests[0].reminders[0]
                self.assertIn("<hook-reminder", reminder)
                self.assertIn("event=\"message.before_model\"", reminder)
                self.assertIn("This is internal automation context", reminder)
                self.assertIn("Remember the hook reminder.", reminder)
                self.assertIn("do not quote, summarize, acknowledge", reminder)

        asyncio.run(run())

    def test_turn_end_hook_results_are_written_to_run_record_metadata(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                hooks_path = root / ".codeagent" / "hooks.local.yaml"
                hooks_path.parent.mkdir()
                hooks_path.write_text(
                    yaml.safe_dump(
                        {
                            "hooks": [
                                {
                                    "event": "turn.end",
                                    "action": {
                                        "type": "command",
                                        "argv": [sys.executable, "-c", "print('hook-finished')"],
                                    },
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                client = FakeStreamingClient(["Final Answer: done"])
                session = PCodeAgentSession(
                    client,
                    build_default_registry(root),
                    AgentConfig(max_steps=1),
                    hook_manager=HookManager.load_for_workspace(root, include_user_hooks=False),
                )

                result = await session.run_turn("answer", FakeEvents())
                assert result.record_path is not None
                record = json.loads(Path(result.record_path).read_text(encoding="utf-8"))

                self.assertIn("hooks", record["metadata"])
                self.assertIn("hook-finished", json.dumps(record["metadata"], ensure_ascii=False))

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
