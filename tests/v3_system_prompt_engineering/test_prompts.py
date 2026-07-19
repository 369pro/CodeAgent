from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zoneinfo import ZoneInfo

from codeagent.chat import (
    ChatRequest,
    _messages_with_reminders,
    parse_anthropic_usage,
    parse_openai_usage,
)
from codeagent.llm import Message
from codeagent.prompts import (
    PromptBuilder,
    PromptSection,
    build_plan_mode_reminder,
    build_environment_block,
    build_stable_prompt,
    build_system_reminder,
    planning_reminder,
)
from codeagent.tools import build_default_registry


class PromptEngineeringTest(unittest.TestCase):
    def test_prompt_builder_orders_sections_and_skips_blank_content(self) -> None:
        prompt = (
            PromptBuilder()
            .add(PromptSection("later", 20, "later"))
            .add(PromptSection("blank", 10, ""))
            .add(PromptSection("first", 0, "first"))
            .build()
        )

        self.assertEqual(prompt, "first\n\nlater")

    def test_stable_prompt_is_byte_stable_and_skips_empty_slots(self) -> None:
        with TemporaryDirectory() as workspace:
            tools = build_default_registry(workspace)

            first = build_stable_prompt(tools)
            second = build_stable_prompt(tools)

        self.assertEqual(first, second)
        self.assertIn("You are CodeAgent", first)
        self.assertIn("Do NOT use the Bash tool", first)
        self.assertIn("Call read_file before editing", first)
        self.assertIn("# Available tools", first)
        self.assertNotIn("CustomInstructions", first)
        self.assertNotIn("\n\n\n", first)

    def test_tool_definitions_reinforce_key_conventions(self) -> None:
        with TemporaryDirectory() as workspace:
            stable = build_stable_prompt(build_default_registry(workspace))

        self.assertIn("Prefer dedicated tools when available", stable)
        self.assertIn("Existing files must be read first", stable)
        self.assertIn("The file must be read first", stable)

    def test_environment_block_is_dynamic_and_not_part_of_stable_prompt(self) -> None:
        with TemporaryDirectory() as workspace:
            root = Path(workspace)
            tools = build_default_registry(root)
            stable = build_stable_prompt(tools)
            env = build_environment_block(
                root,
                "test-model",
                now=datetime(2026, 7, 18, 14, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
            )

        self.assertIn(" - Working directory:", env)
        self.assertIn(" - Current time: 2026-07-18 14:30:00 CST", env)
        self.assertIn(" - Current model: test-model", env)
        self.assertNotIn("2026-07-18", stable)
        self.assertNotIn("test-model", stable)

    def test_system_reminder_merges_into_last_user_message_without_history_mutation(
        self,
    ) -> None:
        history = [
            Message("user", "hello"),
            Message("assistant", "hi"),
            Message("user", "next"),
        ]
        request = ChatRequest(
            stable_prompt="stable",
            environment="env",
            reminders=[build_system_reminder("remember this")],
            messages=history,
        )

        messages = _messages_with_reminders(request)

        self.assertEqual(history[-1].content, "next")
        self.assertEqual(
            [message["role"] for message in messages], ["user", "assistant", "user"]
        )
        self.assertIn("<system-reminder>", messages[-1]["content"])
        self.assertTrue(messages[-1]["content"].endswith("next"))

    def test_planning_reminder_frequency(self) -> None:
        self.assertIn("Plan workflow", planning_reminder(1))
        self.assertIn("Stay read-only", planning_reminder(2))
        self.assertIn("Plan workflow", planning_reminder(5))

    def test_plan_mode_reminder_matches_current_tool_protocol(self) -> None:
        reminder = build_plan_mode_reminder(
            ".codeagent/plans/current.md", plan_exists=False, iteration=1
        )

        self.assertIn("Action: write_file", reminder)
        self.assertIn("Action Input:", reminder)
        self.assertIn("Action: exit_plan_mode", reminder)
        self.assertIn("Do not use XML, DSML, tool_calls blocks", reminder)
        self.assertNotIn("Agent tool", reminder)
        self.assertNotIn("subagent_type", reminder)

    def test_usage_parsers_default_missing_cache_fields_to_zero(self) -> None:
        openai_usage = parse_openai_usage(
            {
                "prompt_tokens": 10,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 7},
            }
        )
        anthropic_usage = parse_anthropic_usage(
            {
                "input_tokens": 11,
                "output_tokens": 4,
                "cache_creation_input_tokens": 5,
                "cache_read_input_tokens": 6,
            }
        )
        empty_openai_usage = parse_openai_usage({"prompt_tokens": 1})

        self.assertIsNotNone(openai_usage)
        assert openai_usage is not None
        self.assertEqual(openai_usage.input_tokens, 10)
        self.assertEqual(openai_usage.output_tokens, 3)
        self.assertEqual(openai_usage.cache_read_tokens, 7)
        self.assertIsNotNone(anthropic_usage)
        assert anthropic_usage is not None
        self.assertEqual(anthropic_usage.cache_write_tokens, 5)
        self.assertEqual(anthropic_usage.cache_read_tokens, 6)
        self.assertIsNotNone(empty_openai_usage)
        assert empty_openai_usage is not None
        self.assertEqual(empty_openai_usage.cache_read_tokens, 0)


if __name__ == "__main__":
    unittest.main()
