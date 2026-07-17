from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codeagent.config import load_config


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


if __name__ == "__main__":
    unittest.main()
