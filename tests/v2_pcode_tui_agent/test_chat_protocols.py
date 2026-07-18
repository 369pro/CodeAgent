from __future__ import annotations

import unittest

from codeagent.chat import parse_anthropic_stream_data, parse_openai_stream_data
from codeagent.llm import LLMError


class ChatProtocolTest(unittest.TestCase):
    def test_parse_openai_content_delta_ignores_reasoning(self) -> None:
        chunks, usage = parse_openai_stream_data(
            '{"choices":[{"delta":{"reasoning_content":"hidden","content":"hello"}}]}'
        )

        self.assertEqual(chunks, ["hello"])
        self.assertIsNone(usage)

    def test_parse_anthropic_text_delta_ignores_thinking(self) -> None:
        chunks, usage = parse_anthropic_stream_data(
            '{"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"hidden"}}'
        )
        self.assertEqual(chunks, [])
        self.assertIsNone(usage)

        chunks, usage = parse_anthropic_stream_data(
            '{"type":"content_block_delta","delta":{"type":"text_delta","text":"visible"}}'
        )
        self.assertEqual(chunks, ["visible"])
        self.assertIsNone(usage)

    def test_parse_anthropic_error_event(self) -> None:
        with self.assertRaisesRegex(LLMError, "bad key"):
            parse_anthropic_stream_data(
                '{"type":"error","error":{"message":"bad key"}}'
            )


if __name__ == "__main__":
    unittest.main()
