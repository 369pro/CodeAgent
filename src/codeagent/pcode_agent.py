from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Protocol

from codeagent.chat import ChatRequest, StreamingChatClient
from codeagent.config import AgentConfig
from codeagent.llm import LLMError, Message
from codeagent.records import RunRecorder, utc_now
from codeagent.tools import ToolRegistry


ACTION_RE = re.compile(r"Action:\s*(?P<name>[A-Za-z_][\w-]*)\s+Action Input:\s*(?P<input>\{.*\})", re.DOTALL)
FINAL_RE = re.compile(r"Final Answer:\s*(?P<answer>.*)", re.DOTALL)


class TurnEvents(Protocol):
    async def tool_started(self, name: str) -> None:
        ...

    async def tool_finished(self, name: str, is_error: bool) -> None:
        ...

    async def final_delta(self, text: str) -> None:
        ...


@dataclass
class PCodeTurnResult:
    answer: str
    record_path: str | None = None
    tool_statuses: list[str] = field(default_factory=list)


class PCodeAgentSession:
    def __init__(
        self,
        client: StreamingChatClient,
        tools: ToolRegistry,
        config: AgentConfig | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.client = client
        self.tools = tools
        self.config = config or AgentConfig()
        self.system_prompt = system_prompt or build_system_prompt(tools)
        self.history: list[Message] = []

    async def run_turn(self, user_input: str, events: TurnEvents) -> PCodeTurnResult:
        recorder = RunRecorder(self.tools.context.workspace_root)
        recorder.start(user_input)
        self.history.append(Message("user", user_input))
        tool_statuses: list[str] = []

        try:
            for _ in range(self.config.max_steps):
                step_started_at = utc_now()
                output = await self._stream_llm_output(events)
                action = ACTION_RE.search(output)
                if action:
                    tool_name = action.group("name")
                    await events.tool_started(tool_name)
                    try:
                        tool_input = json.loads(action.group("input"))
                        if not isinstance(tool_input, dict):
                            raise ValueError("Action Input must be a JSON object.")
                    except Exception as exc:  # noqa: BLE001 - invalid model output becomes an observation.
                        tool_input = {}
                        result_text = f"Invalid Action Input: {exc}"
                        is_error = True
                    else:
                        result = self.tools.run(tool_name, tool_input)
                        result_text = result.output if not result.is_error else f"ERROR: {result.output}"
                        is_error = result.is_error

                    await events.tool_finished(tool_name, is_error)
                    tool_statuses.append(f"{tool_name}: {'failed' if is_error else 'done'}")
                    self.history.append(Message("assistant", output))
                    self.history.append(Message("user", f"Observation: {result_text}"))
                    recorder.record_step(
                        llm_output=output,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        observation=result_text,
                        is_error=is_error,
                        started_at=step_started_at,
                    )
                    continue

                final = FINAL_RE.search(output)
                if final:
                    answer = final.group("answer").strip()
                    await events.final_delta(answer)
                    self.history.append(Message("assistant", output))
                    recorder.record_step(llm_output=output, started_at=step_started_at)
                    recorder.complete(answer)
                    record_path = recorder.save()
                    return PCodeTurnResult(answer=answer, record_path=str(record_path), tool_statuses=tool_statuses)

                if not action:
                    observation = "Invalid response. Use either Action/Action Input or Final Answer."
                    self.history.append(Message("assistant", output))
                    self.history.append(Message("user", f"Observation: {observation}"))
                    recorder.record_step(
                        llm_output=output,
                        observation=observation,
                        is_error=True,
                        started_at=step_started_at,
                    )
                    continue

            error = f"ReAct loop exceeded max_steps={self.config.max_steps}."
            recorder.fail(error, status="max_steps_exceeded")
            recorder.save()
            raise LLMError(error)
        except Exception as exc:
            if recorder.record.status == "running":
                recorder.fail(f"{type(exc).__name__}: {exc}")
                recorder.save()
            raise

    async def _stream_llm_output(self, events: TurnEvents) -> str:
        request = ChatRequest(system_prompt=self.system_prompt, messages=list(self.history))
        parts: list[str] = []

        async for chunk in self.client.stream(request):
            parts.append(chunk)
        return "".join(parts)


def build_system_prompt(tools: ToolRegistry) -> str:
    return (
        "You are PCode, a terminal coding agent.\n"
        "Use tools when needed, then answer the user clearly and concisely.\n"
        "Do not reveal hidden chain-of-thought. Keep Thought text brief because it is for tool routing only.\n\n"
        "File lookup rules:\n"
        "- If the user names a file without a path, call find_file first with the filename.\n"
        "- If find_file returns one or more paths, call read_file with the matching path before analyzing.\n"
        "- Do not conclude a file is missing after only checking the workspace root.\n\n"
        "Available tools:\n"
        f"{tools.descriptions()}\n\n"
        "Respond in exactly one of these formats:\n"
        "Thought: <brief private routing note>\n"
        "Action: <tool_name>\n"
        "Action Input: <JSON object>\n\n"
        "or:\n"
        "Final Answer: <answer>"
    )
