from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from codeagent.agent import ReActAgent
from codeagent.config import AgentConfig
from codeagent.llm import Message
from codeagent.tools import build_default_registry


class FakeLLM:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.messages: list[list[Message]] = []

    def complete(self, messages: list[Message]) -> str:
        self.messages.append(list(messages))
        return self.outputs.pop(0)


class ReActAgentTest(unittest.TestCase):
    def test_react_loop_calls_three_tools_and_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            (root / "README.md").write_text("CodeAgent\n", encoding="utf-8")
            (root / "src").mkdir()
            (root / "src" / "app.py").write_text("print('react')\n", encoding="utf-8")
            llm = FakeLLM(
                [
                    'Thought: inspect files\nAction: glob\nAction Input: {"pattern": "**/*", "path": "."}',
                    'Thought: read readme\nAction: read_file\nAction Input: {"path": "README.md"}',
                    'Thought: search code\nAction: grep\nAction Input: {"pattern": "react", "path": "."}',
                    "Final Answer: Found CodeAgent docs and one react mention in src/app.py.",
                ]
            )

            result = ReActAgent(
                llm=llm,
                tools=build_default_registry(root),
                config=AgentConfig(max_steps=5),
            ).run("Summarize this workspace.")

            self.assertEqual(result.answer, "Found CodeAgent docs and one react mention in src/app.py.")
            self.assertEqual([step.tool_name for step in result.steps[:-1]], ["glob", "read_file", "grep"])
            self.assertIn("README.md", result.steps[0].observation or "")
            self.assertIn("CodeAgent", result.steps[1].observation or "")
            self.assertIn("src/app.py:1", result.steps[2].observation or "")
            self.assertIsNotNone(result.record_path)
            record = json.loads(Path(result.record_path or "").read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "completed")
            self.assertEqual(record["steps"][0]["tool_name"], "glob")

    def test_react_loop_reports_unknown_tool_as_observation(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            llm = FakeLLM(
                [
                    'Thought: try missing\nAction: missing_tool\nAction Input: {"path": "."}',
                    "Final Answer: I recovered from the tool error.",
                ]
            )

            result = ReActAgent(llm, build_default_registry(workspace), AgentConfig(max_steps=3)).run("Try it.")

            self.assertEqual(result.answer, "I recovered from the tool error.")
            self.assertEqual(result.steps[0].observation, "ERROR: Unknown tool: missing_tool")
            self.assertTrue(result.steps[0].is_error)


if __name__ == "__main__":
    unittest.main()
