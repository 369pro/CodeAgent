from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import unittest

from codeagent.cli import _run_once
from codeagent.llm import LLMError


class FailingAgent:
    def run(self, prompt: str) -> object:
        raise LLMError("DeepSeek HTTP 401: Unauthorized")


class CliTest(unittest.TestCase):
    def test_run_once_prints_llm_error_without_traceback(self) -> None:
        output = StringIO()

        with redirect_stdout(output):
            _run_once(FailingAgent(), "hello")  # type: ignore[arg-type]

        self.assertIn("LLM error: DeepSeek HTTP 401", output.getvalue())


if __name__ == "__main__":
    unittest.main()
