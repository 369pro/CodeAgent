from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import asyncio
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from codeagent.agent import ReActAgent
from codeagent.chat import ChatRequest
from codeagent.config import AgentConfig
from codeagent.llm import Message
from codeagent.observability import build_tracer_from_env, tracing_status_from_env
from codeagent.pcode_agent import PCodeAgentSession
from codeagent.tools import build_default_registry


@dataclass
class RecordedObservation:
    kind: str
    name: str
    parent: str | None
    input: object = None
    output: object = None
    metadata: dict[str, object] = field(default_factory=dict)

    def update(self, **kwargs: object) -> None:
        if "output" in kwargs:
            self.output = kwargs["output"]
        if "metadata" in kwargs and isinstance(kwargs["metadata"], dict):
            self.metadata.update(kwargs["metadata"])


class RecordingContext:
    def __init__(self, tracer: RecordingTracer, observation: RecordedObservation) -> None:
        self.tracer = tracer
        self.observation = observation

    def __enter__(self) -> RecordedObservation:
        self.tracer.stack.append(self.observation.name)
        return self.observation

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> object:
        self.tracer.stack.pop()
        return False


class RecordingTracer:
    def __init__(self) -> None:
        self.observations: list[RecordedObservation] = []
        self.stack: list[str] = []
        self.flush_count = 0

    def start_run(self, *, name: str, user_input: str, metadata: dict[str, object]) -> RecordingContext:
        return self._record("span", name, user_input, metadata)

    def start_generation(
        self,
        *,
        name: str,
        model: str | None,
        input: object,
        metadata: dict[str, object],
    ) -> RecordingContext:
        details = dict(metadata)
        if model:
            details["model"] = model
        return self._record("generation", name, input, details)

    def start_tool(self, *, name: str, input: object) -> RecordingContext:
        return self._record("span", f"tool:{name}", input, {})

    def flush(self) -> None:
        self.flush_count += 1

    def _record(self, kind: str, name: str, input: object, metadata: dict[str, object]) -> RecordingContext:
        observation = RecordedObservation(
            kind=kind,
            name=name,
            parent=self.stack[-1] if self.stack else None,
            input=input,
            metadata=dict(metadata),
        )
        self.observations.append(observation)
        return RecordingContext(self, observation)


class FakeLLM:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs

    def complete(self, messages: list[Message]) -> str:
        return self.outputs.pop(0)


class FakeStreamingClient:
    async def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        output = 'Thought: inspect\nAction: read_file\nAction Input: {"path":"README.md"}'
        if len(request.messages) > 1:
            output = "Final Answer: streamed answer"
        for index in range(0, len(output), 8):
            yield output[index : index + 8]


class FakeEvents:
    async def tool_started(self, name: str) -> None:
        return None

    async def tool_finished(self, name: str, is_error: bool) -> None:
        return None

    async def final_delta(self, text: str) -> None:
        return None


class ObservabilityTest(unittest.TestCase):
    def test_missing_langfuse_env_uses_noop_tracer(self) -> None:
        tracer = build_tracer_from_env()

        with tracer.start_run(name="test", user_input="hello", metadata={}) as observation:
            observation.update(output="ok")

        tracer.flush()

    def test_tracing_status_does_not_expose_keys(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "LANGFUSE_PUBLIC_KEY": "pk-lf-secret",
                "LANGFUSE_SECRET_KEY": "sk-lf-secret",
                "LANGFUSE_BASE_URL": "http://localhost:3000",
            },
            clear=True,
        ):
            status = tracing_status_from_env()

        self.assertEqual(status, "trace: langfuse http://localhost:3000")
        self.assertNotIn("pk-lf-secret", status)
        self.assertNotIn("sk-lf-secret", status)

    def test_tracing_status_reports_off_without_keys(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(tracing_status_from_env(), "trace: off")

    def test_react_run_traces_one_turn_with_nested_generation_and_tool(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            (root / "README.md").write_text("hello\n", encoding="utf-8")
            tracer = RecordingTracer()
            agent = ReActAgent(
                llm=FakeLLM(
                    [
                        'Thought: inspect\nAction: read_file\nAction Input: {"path":"README.md"}',
                        "Final Answer: done",
                    ]
                ),
                tools=build_default_registry(root),
                config=AgentConfig(max_steps=3),
                tracer=tracer,
            )

            result = agent.run("Read the README.")

        self.assertEqual(result.answer, "done")
        self.assertEqual([item.name for item in tracer.observations], ["react.run", "react.llm", "tool:read_file", "react.llm"])
        self.assertEqual([item.parent for item in tracer.observations], [None, "react.run", "react.run", "react.run"])
        self.assertEqual(tracer.observations[0].output, "done")
        self.assertIn("hello", str(tracer.observations[2].output))
        self.assertEqual(tracer.flush_count, 1)

    def test_pcode_turn_traces_one_turn_with_nested_generation_and_tool(self) -> None:
        async def run() -> RecordingTracer:
            with tempfile.TemporaryDirectory() as workspace:
                root = Path(workspace)
                (root / "README.md").write_text("hello\n", encoding="utf-8")
                tracer = RecordingTracer()
                session = PCodeAgentSession(
                    client=FakeStreamingClient(),
                    tools=build_default_registry(root),
                    config=AgentConfig(max_steps=3),
                    tracer=tracer,
                )

                result = await session.run_turn("Read the README.", FakeEvents())

            self.assertEqual(result.answer, "streamed answer")
            return tracer

        tracer = asyncio.run(run())

        self.assertEqual([item.name for item in tracer.observations], ["pcode.turn", "pcode.llm", "tool:read_file", "pcode.llm"])
        self.assertEqual([item.parent for item in tracer.observations], [None, "pcode.turn", "pcode.turn", "pcode.turn"])
        self.assertEqual(tracer.observations[0].output, "streamed answer")
        self.assertIn("hello", str(tracer.observations[2].output))
        self.assertEqual(tracer.flush_count, 1)


if __name__ == "__main__":
    unittest.main()
