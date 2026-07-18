from __future__ import annotations

from collections.abc import AsyncIterator
import asyncio
import tempfile
import unittest
from pathlib import Path

from codeagent.chat import ChatRequest, TextDelta, UsageDelta
from codeagent.prompts import GenerationUsage
from codeagent.config import AgentConfig
from codeagent.pcode_agent import PCodeAgentSession
from codeagent.tools import build_default_registry


class FakeStreamingClient:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.requests: list[ChatRequest] = []

    async def stream(self, request: ChatRequest) -> AsyncIterator[TextDelta]:
        self.requests.append(request)
        output = self.outputs.pop(0)
        for index in range(0, len(output), 7):
            yield TextDelta(output[index : index + 7])


class FakeEvents:
    def __init__(self) -> None:
        self.tools: list[str] = []
        self.final = ""

    async def tool_started(self, name: str) -> None:
        self.tools.append(f"{name}:started")

    async def tool_finished(self, name: str, is_error: bool) -> None:
        self.tools.append(f"{name}:{'failed' if is_error else 'done'}")

    async def final_delta(self, text: str) -> None:
        self.final += text


class PCodeAgentSessionTest(unittest.TestCase):
    def test_runs_react_tools_and_streams_only_final_answer(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                (root / "README.md").write_text("hello pcode\n", encoding="utf-8")
                client = FakeStreamingClient(
                    [
                        'Thought: inspect\nAction: read_file\nAction Input: {"path":"README.md"}',
                        "Final Answer: The README says hello pcode.",
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=3),
                )

                result = await session.run_turn("Read the README.", events)

                self.assertEqual(result.answer, "The README says hello pcode.")
                self.assertEqual(events.tools, ["read_file:started", "read_file:done"])
                self.assertEqual(events.final.strip(), "The README says hello pcode.")
                self.assertIn("hello pcode", session.history[-2].content)

        asyncio.run(run())

    def test_runs_action_when_action_input_is_on_same_line(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                (root / "agent.py").write_text("print('agent')\n", encoding="utf-8")
                client = FakeStreamingClient(
                    [
                        '让我先确认 agent.py 文件的位置。\nAction: glob Action Input: {"pattern": "**/agent.py"}',
                        "Final Answer: 找到了 agent.py。",
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=3),
                )

                result = await session.run_turn("看看agent.py,分析一下", events)

                self.assertEqual(result.answer, "找到了 agent.py。")
                self.assertEqual(events.tools, ["glob:started", "glob:done"])
                self.assertIn("agent.py", session.history[-2].content)

        asyncio.run(run())

    def test_action_takes_precedence_over_misplaced_final_answer(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                (root / "agent.py").write_text("print('agent')\n", encoding="utf-8")
                client = FakeStreamingClient(
                    [
                        'Final Answer: 让我先确认 agent.py 文件的位置。\nAction: glob Action Input: {"pattern": "**/agent.py"}',
                        "Final Answer: 已读取工具结果。",
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=3),
                )

                result = await session.run_turn("看看agent.py,分析一下", events)

                self.assertEqual(result.answer, "已读取工具结果。")
                self.assertEqual(events.tools, ["glob:started", "glob:done"])
                self.assertEqual(events.final, "已读取工具结果。")

        asyncio.run(run())

    def test_can_locate_nested_file_by_name_before_analysis(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                (root / "src" / "codeagent").mkdir(parents=True)
                (root / "src" / "codeagent" / "chat.py").write_text(
                    "class ProviderStreamingClient: ...\n", encoding="utf-8"
                )
                client = FakeStreamingClient(
                    [
                        'Thought: locate file\nAction: find_file\nAction Input: {"name": "chat.py"}',
                        'Thought: read file\nAction: read_file\nAction Input: {"path": "src/codeagent/chat.py"}',
                        "Final Answer: chat.py defines ProviderStreamingClient.",
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=4),
                )

                result = await session.run_turn("分析 chat.py", events)

                self.assertEqual(
                    result.answer, "chat.py defines ProviderStreamingClient."
                )
                self.assertEqual(
                    events.tools,
                    [
                        "find_file:started",
                        "find_file:done",
                        "read_file:started",
                        "read_file:done",
                    ],
                )
                self.assertIn("src/codeagent/chat.py", session.history[-4].content)
                self.assertIn("ProviderStreamingClient", session.history[-2].content)

        asyncio.run(run())

    def test_planning_mode_uses_read_only_tool_surface_and_reminder(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                (root / "README.md").write_text("hello\n", encoding="utf-8")
                client = FakeStreamingClient(
                    [
                        'Thought: try write\nAction: write_file\nAction Input: {"path":"README.md","content":"changed"}',
                        "Final Answer: I will only plan.",
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=3),
                )

                result = await session.run_turn(
                    "plan an edit", events, planning_mode=True
                )

                self.assertEqual(result.answer, "I will only plan.")
                self.assertEqual(
                    events.tools, ["write_file:started", "write_file:failed"]
                )
                tool_definitions = client.requests[0].stable_prompt.split("# Available tools", 1)[1]
                self.assertNotIn(" - write_file:", tool_definitions)
                self.assertIn("<system-reminder>", client.requests[0].reminders[0])
                self.assertNotIn(
                    "<system-reminder>", client.requests[0].messages[-1].content
                )
                self.assertEqual(
                    (root / "README.md").read_text(encoding="utf-8"), "hello\n"
                )

        asyncio.run(run())

    def test_generation_usage_is_recorded(self) -> None:
        class UsageStreamingClient(FakeStreamingClient):
            async def stream(
                self, request: ChatRequest
            ) -> AsyncIterator[TextDelta | UsageDelta]:
                self.requests.append(request)
                yield TextDelta("Final Answer: done")
                yield UsageDelta(
                    GenerationUsage(
                        input_tokens=10, output_tokens=2, cache_read_tokens=7
                    )
                )

        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                client = UsageStreamingClient([])
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(workspace),
                    config=AgentConfig(max_steps=1),
                )

                result = await session.run_turn("answer", FakeEvents())
                assert result.record_path is not None
                record_text = Path(result.record_path).read_text(encoding="utf-8")

            self.assertIn('"input_tokens": 10', record_text)
            self.assertIn('"cache_read_tokens": 7', record_text)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
