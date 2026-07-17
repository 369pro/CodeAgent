from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codeagent.config import ConfigError, load_config


class ConfigTest(unittest.TestCase):
    def test_loads_deepseek_config_with_inline_key(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            config_path = Path(workspace) / "config.yaml"
            config_path.write_text(
                """
llm:
  provider: deepseek
  base_url: https://api.deepseek.com/v1
  api_key: test-key
  model: deepseek-v4-flash
agent:
  max_steps: 3
  tool_output_limit: 123
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.llm.base_url, "https://api.deepseek.com/v1")
            self.assertEqual(config.llm.model, "deepseek-v4-flash")
            self.assertEqual(config.llm.resolved_api_key, "test-key")
            self.assertEqual(config.agent.max_steps, 3)
            self.assertEqual(config.agent.tool_output_limit, 123)

    def test_loads_provider_list_and_env_key(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            config_path = Path(workspace) / "config.yaml"
            config_path.write_text(
                """
providers:
  - name: OpenAI
    protocol: openai
    base_url: https://api.openai.com/v1
    api_key: env:OPENAI_API_KEY
    model: gpt-5
    thinking: true
    reasoning_effort: high
  - name: Claude
    protocol: anthropic
    endpoint: https://proxy.example/messages
    api_key: inline-key
    model: claude
    thinking_budget_tokens: 2048
""",
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"OPENAI_API_KEY": "resolved"}, clear=False):
                config = load_config(config_path)

            self.assertEqual(len(config.providers), 2)
            self.assertEqual(config.providers[0].resolved_api_key, "resolved")
            self.assertEqual(config.providers[0].request_endpoint, "https://api.openai.com/v1/chat/completions")
            self.assertEqual(config.providers[1].request_endpoint, "https://proxy.example/messages")

    def test_rejects_invalid_provider_at_startup(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            config_path = Path(workspace) / "config.yaml"
            config_path.write_text(
                """
providers:
  - name: Broken
    protocol: openai
    model: gpt
    api_key: env:MISSING_KEY
""",
                encoding="utf-8",
            )

            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaisesRegex(ConfigError, "MISSING_KEY"):
                    load_config(config_path)


if __name__ == "__main__":
    unittest.main()
