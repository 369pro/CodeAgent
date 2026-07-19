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

    def test_runs_simple_xml_tool_call_fallback(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                (root / "src" / "codeagent").mkdir(parents=True)
                (root / "src" / "codeagent" / "llm.py").write_text(
                    "class DeepSeekChatClient: ...\n", encoding="utf-8"
                )
                client = FakeStreamingClient(
                    [
                        "Let me find and read the file.\n\n"
                        "<tool_call>\nfind_file\nname\nllm.py\n</tool_call>",
                        "Final Answer: 找到了 llm.py。",
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=3),
                )

                result = await session.run_turn("总结llm.py的内容", events)

                self.assertEqual(result.answer, "找到了 llm.py。")
                self.assertEqual(events.tools, ["find_file:started", "find_file:done"])
                self.assertIn("src/codeagent/llm.py", session.history[-2].content)

        asyncio.run(run())

    def test_runs_dsml_tool_call_fallback(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                (root / "src" / "codeagent").mkdir(parents=True)
                (root / "src" / "codeagent" / "llm.py").write_text(
                    "class DeepSeekChatClient: ...\n", encoding="utf-8"
                )
                client = FakeStreamingClient(
                    [
                        '<| DSML | tool_calls>\n'
                        '<| DSML | invoke name="find_file">\n'
                        '<| DSML | parameter name="name" string="true">llm.py</| DSML | parameter>\n'
                        "</| DSML | invoke>\n"
                        "</| DSML | tool_calls>",
                        "Final Answer: 找到了 llm.py。",
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=3),
                )

                result = await session.run_turn("总结llm.py的内容", events)

                self.assertEqual(result.answer, "找到了 llm.py。")
                self.assertEqual(events.tools, ["find_file:started", "find_file:done"])
                self.assertIn("src/codeagent/llm.py", session.history[-2].content)

        asyncio.run(run())

    def test_runs_fullwidth_pipe_dsml_tool_call_fallback(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                (root / "src" / "codeagent").mkdir(parents=True)
                (root / "src" / "codeagent" / "llm.py").write_text(
                    "class DeepSeekChatClient: ...\n", encoding="utf-8"
                )
                client = FakeStreamingClient(
                    [
                        '<｜｜DSML｜｜tool_calls>\n'
                        '<｜｜DSML｜｜invoke name="find_file">\n'
                        '<｜｜DSML｜｜parameter name="name" string="true">llm.py</｜｜DSML｜｜parameter>\n'
                        "</｜｜DSML｜｜invoke>\n"
                        "</｜｜DSML｜｜tool_calls>",
                        "Final Answer: 找到了 llm.py。",
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=3),
                )

                result = await session.run_turn("总结llm.py的内容", events)

                self.assertEqual(result.answer, "找到了 llm.py。")
                self.assertEqual(events.tools, ["find_file:started", "find_file:done"])
                self.assertIn("src/codeagent/llm.py", session.history[-2].content)

        asyncio.run(run())

    def test_runs_named_xml_tool_call_fallback(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                (root / "src" / "codeagent").mkdir(parents=True)
                (root / "src" / "codeagent" / "llm.py").write_text(
                    "class DeepSeekChatClient: ...\n", encoding="utf-8"
                )
                client = FakeStreamingClient(
                    [
                        "<find_file>\n"
                        "<name>llm.py</name>\n"
                        f"<path>{root}</path>\n"
                        "</find_file>",
                        "Final Answer: 找到了 llm.py。",
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=3),
                )

                result = await session.run_turn("总结llm.py的内容", events)

                self.assertEqual(result.answer, "找到了 llm.py。")
                self.assertEqual(events.tools, ["find_file:started", "find_file:done"])
                self.assertIn("src/codeagent/llm.py", session.history[-2].content)

        asyncio.run(run())

    def test_runs_named_xml_glob_tool_call_fallback(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                (root / "src" / "codeagent").mkdir(parents=True)
                (root / "src" / "codeagent" / "llm.py").write_text(
                    "class DeepSeekChatClient: ...\n", encoding="utf-8"
                )
                client = FakeStreamingClient(
                    [
                        "<glob>\n"
                        "<pattern>**/llm.py</pattern>\n"
                        f"<path>{root}</path>\n"
                        "</glob>",
                        "Final Answer: 找到了 llm.py。",
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=3),
                )

                result = await session.run_turn("总结llm.py的内容", events)

                self.assertEqual(result.answer, "找到了 llm.py。")
                self.assertEqual(events.tools, ["glob:started", "glob:done"])
                self.assertIn("src/codeagent/llm.py", session.history[-2].content)

        asyncio.run(run())

    def test_repeated_same_tool_failures_open_circuit_breaker(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                client = FakeStreamingClient(
                    [
                        'Thought: read\nAction: read_file\nAction Input: {"path":"missing.txt"}',
                        'Thought: retry\nAction: read_file\nAction Input: {"path":"missing.txt"}',
                        'Thought: retry again\nAction: read_file\nAction Input: {"path":"missing.txt"}',
                        'Thought: retry fourth\nAction: read_file\nAction Input: {"path":"missing.txt"}',
                        "Final Answer: I will stop retrying read_file.",
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(workspace),
                    config=AgentConfig(max_steps=5),
                )

                result = await session.run_turn("read missing file", events)

                self.assertEqual(result.answer, "I will stop retrying read_file.")
                self.assertEqual(
                    events.tools,
                    [
                        "read_file:started",
                        "read_file:failed",
                        "read_file:started",
                        "read_file:failed",
                        "read_file:started",
                        "read_file:failed",
                        "read_file:started",
                        "read_file:failed",
                    ],
                )
                self.assertIn("Tool circuit breaker opened", session.history[-4].content)
                self.assertIn("Tool circuit breaker open", session.history[-2].content)

        asyncio.run(run())

    def test_plain_answer_after_tool_call_is_treated_as_final_answer(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                (root / "llm.py").write_text("class Message: ...\n", encoding="utf-8")
                client = FakeStreamingClient(
                    [
                        'Thought: read\nAction: read_file\nAction Input: {"path":"llm.py"}',
                        "**llm.py** 定义了 Message 等 LLM 通信相关结构。",
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=3),
                )

                result = await session.run_turn("总结llm.py内容", events)

                self.assertEqual(
                    result.answer,
                    "**llm.py** 定义了 Message 等 LLM 通信相关结构。",
                )
                self.assertEqual(events.final, result.answer)
                self.assertNotIn("Invalid response", session.history[-1].content)

        asyncio.run(run())

    def test_planning_mode_allows_only_plan_file_writes(self) -> None:
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
                self.assertIn(" - write_file:", tool_definitions)
                self.assertNotIn(" - edit_file:", tool_definitions)
                self.assertIn(" - exit_plan_mode:", tool_definitions)
                self.assertNotIn(" - bash:", tool_definitions)
                self.assertIn("Plan mode is active", client.requests[0].reminders[0])
                self.assertIn(".codeagent/plans/current.md", client.requests[0].reminders[0])
                self.assertNotIn(
                    "<system-reminder>", client.requests[0].messages[-1].content
                )
                self.assertEqual(
                    (root / "README.md").read_text(encoding="utf-8"), "hello\n"
                )

        asyncio.run(run())

    def test_planning_mode_does_not_expose_edit_file(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                (root / "README.md").write_text("hello\n", encoding="utf-8")
                client = FakeStreamingClient(
                    [
                        'Thought: bad edit\nAction: edit_file\nAction Input: {"path":"README.md","old":"hello","new":"changed"}',
                        "Final Answer: I will revise the plan instead.",
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=3),
                )

                result = await session.run_turn(
                    "revise the plan", events, planning_mode=True
                )

                self.assertEqual(result.answer, "I will revise the plan instead.")
                self.assertEqual(events.tools, ["edit_file:started", "edit_file:failed"])
                self.assertEqual(
                    (root / "README.md").read_text(encoding="utf-8"), "hello\n"
                )
                self.assertIn("Observation: ERROR: Unknown tool: edit_file", session.history[-2].content)

        asyncio.run(run())

    def test_planning_mode_exits_after_non_empty_plan_file(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                plan_path = root / ".codeagent" / "plans" / "current.md"
                client = FakeStreamingClient(
                    [
                        'Thought: write plan\nAction: write_file\nAction Input: {"path":".codeagent/plans/current.md","content":"# Plan\\n\\n1. Update README."}',
                        'Thought: done\nAction: exit_plan_mode\nAction Input: {}',
                    ]
                )
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=3),
                )

                result = await session.run_turn(
                    "plan an edit", events, planning_mode=True, plan_path=plan_path
                )

                self.assertTrue(result.plan_ready)
                self.assertEqual(result.plan_path, str(plan_path.resolve(strict=False)))
                self.assertEqual(result.answer, "Plan ready for approval.")
                self.assertEqual(
                    events.tools,
                    [
                        "write_file:started",
                        "write_file:done",
                        "exit_plan_mode:started",
                        "exit_plan_mode:done",
                    ],
                )
                self.assertIn("# Plan", plan_path.read_text(encoding="utf-8"))

        asyncio.run(run())

    def test_planning_mode_final_answer_with_plan_file_is_ready_for_approval(self) -> None:
        async def run() -> None:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                plan_path = root / ".codeagent" / "plans" / "current.md"
                plan_path.parent.mkdir(parents=True)
                plan_path.write_text("# Plan\n\n1. Update README.\n", encoding="utf-8")
                client = FakeStreamingClient(["Final Answer: 计划已写好。"])
                events = FakeEvents()
                session = PCodeAgentSession(
                    client=client,
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=1),
                )

                result = await session.run_turn(
                    "plan an edit", events, planning_mode=True, plan_path=plan_path
                )

                self.assertTrue(result.plan_ready)
                self.assertEqual(result.plan_path, str(plan_path.resolve(strict=False)))
                self.assertEqual(result.answer, "计划已写好。")

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
