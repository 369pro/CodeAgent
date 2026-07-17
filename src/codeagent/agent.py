from __future__ import annotations

from dataclasses import dataclass, field
import json
import re

from codeagent.config import AgentConfig
from codeagent.llm import ChatClient, Message
from codeagent.observability import Tracer, build_tracer_from_env
from codeagent.records import RunRecorder, utc_now
from codeagent.tools import ToolRegistry


ACTION_RE = re.compile(r"Action:\s*(?P<name>[A-Za-z_][\w-]*)\s*\nAction Input:\s*(?P<input>\{.*\})", re.DOTALL)
FINAL_RE = re.compile(r"Final Answer:\s*(?P<answer>.*)", re.DOTALL)


@dataclass(frozen=True)
class Step:
    llm_output: str
    tool_name: str | None = None
    tool_input: dict[str, object] | None = None
    observation: str | None = None
    is_error: bool = False


@dataclass(frozen=True)
class RunResult:
    answer: str
    steps: list[Step] = field(default_factory=list)
    record_path: str | None = None


class ReActAgent:
    def __init__(
        self,
        llm: ChatClient,
        tools: ToolRegistry,
        config: AgentConfig | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.config = config or AgentConfig()
        self.tracer = tracer or build_tracer_from_env()

    def run(self, user_input: str) -> RunResult:
        recorder = RunRecorder(self.tools.context.workspace_root)
        recorder.start(user_input)
        try:
            with self.tracer.start_run(
                name="react.run",
                user_input=user_input,
                metadata={"workspace": str(self.tools.context.workspace_root)},
            ) as run_observation:
                try:
                    result = self._run_with_recording(user_input, recorder)
                except Exception as exc:
                    run_observation.update(output={"error": f"{type(exc).__name__}: {exc}"})
                    raise
                run_observation.update(output=result.answer)
                return result
        finally:
            self.tracer.flush()

    def _run_with_recording(self, user_input: str, recorder: RunRecorder) -> RunResult:
        messages = [Message("system", self._system_prompt()), Message("user", user_input)]
        steps: list[Step] = []

        try:
            for _ in range(self.config.max_steps):
                step_started_at = utc_now()
                with self.tracer.start_generation(
                    name="react.llm",
                    model=getattr(getattr(self.llm, "config", None), "model", None),
                    input=[message.__dict__ for message in messages],
                    metadata={"message_count": len(messages)},
                ) as generation:
                    output = self.llm.complete(messages)
                    generation.update(output=output)
                final = FINAL_RE.search(output)
                if final:
                    answer = final.group("answer").strip()
                    steps.append(Step(llm_output=output))
                    recorder.record_step(llm_output=output, started_at=step_started_at)
                    recorder.complete(answer)
                    record_path = recorder.save()
                    return RunResult(answer=answer, steps=steps, record_path=str(record_path))

                action = ACTION_RE.search(output)
                if not action:
                    observation = "Invalid response. Use either Action/Action Input or Final Answer."
                    steps.append(Step(llm_output=output, observation=observation, is_error=True))
                    recorder.record_step(
                        llm_output=output,
                        observation=observation,
                        is_error=True,
                        started_at=step_started_at,
                    )
                    messages.append(Message("assistant", output))
                    messages.append(Message("user", f"Observation: {observation}"))
                    continue

                tool_name = action.group("name")
                try:
                    tool_input = json.loads(action.group("input"))
                    if not isinstance(tool_input, dict):
                        raise ValueError("Action Input must be a JSON object.")
                except Exception as exc:  # noqa: BLE001 - invalid model output becomes an observation.
                    tool_input = {}
                    result_text = f"Invalid Action Input: {exc}"
                    is_error = True
                else:
                    with self.tracer.start_tool(name=tool_name, input=tool_input) as tool_observation:
                        result = self.tools.run(tool_name, tool_input)
                        tool_observation.update(
                            output=result.output,
                            metadata={**result.metadata, "is_error": result.is_error},
                        )
                    result_text = result.output if not result.is_error else f"ERROR: {result.output}"
                    is_error = result.is_error

                steps.append(
                    Step(
                        llm_output=output,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        observation=result_text,
                        is_error=is_error,
                    )
                )
                recorder.record_step(
                    llm_output=output,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    observation=result_text,
                    is_error=is_error,
                    started_at=step_started_at,
                )
                messages.append(Message("assistant", output))
                messages.append(Message("user", f"Observation: {result_text}"))

            error = f"ReAct loop exceeded max_steps={self.config.max_steps}."
            recorder.fail(error, status="max_steps_exceeded")
            recorder.save()
            raise RuntimeError(error)
        except Exception as exc:
            if recorder.record.status == "running":
                recorder.fail(f"{type(exc).__name__}: {exc}")
                recorder.save()
            raise

    def _system_prompt(self) -> str:
        return (
            "You are CodeAgent, a minimal ReAct agent.\n"
            "Use tools when needed, then answer.\n\n"
            "Available tools:\n"
            f"{self.tools.descriptions()}\n\n"
            "Respond in exactly one of these formats:\n"
            "Thought: <brief reasoning>\n"
            "Action: <tool_name>\n"
            "Action Input: <JSON object>\n\n"
            "or:\n"
            "Final Answer: <answer>"
        )
