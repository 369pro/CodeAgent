from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Protocol

from codeagent.chat import ChatRequest, StreamingChatClient, TextDelta, UsageDelta
from codeagent.config import AgentConfig
from codeagent.llm import LLMError, Message
from codeagent.observability import Tracer, build_tracer_from_env
from codeagent.prompts import (
    GenerationUsage,
    build_environment_block,
    build_stable_prompt,
    execute_plan_reminder,
    planning_reminder,
)
from codeagent.records import RunRecorder, utc_now
from codeagent.tools import ToolRegistry


ACTION_RE = re.compile(
    r"Action:\s*(?P<name>[A-Za-z_][\w-]*)\s+Action Input:\s*(?P<input>\{.*\})",
    re.DOTALL,
)
FINAL_RE = re.compile(r"Final Answer:\s*(?P<answer>.*)", re.DOTALL)


class TurnEvents(Protocol):
    async def tool_started(self, name: str) -> None: ...

    async def tool_finished(self, name: str, is_error: bool) -> None: ...

    async def final_delta(self, text: str) -> None: ...


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
        tracer: Tracer | None = None,
    ) -> None:
        self.client = client
        self.tools = tools
        self.config = config or AgentConfig()
        self.system_prompt = system_prompt
        self.history: list[Message] = []
        self.tracer = tracer or build_tracer_from_env()

    async def run_turn(
        self,
        user_input: str,
        events: TurnEvents,
        *,
        planning_mode: bool = False,
        execute_plan: bool = False,
    ) -> PCodeTurnResult:
        recorder = RunRecorder(self.tools.context.workspace_root)
        recorder.start(user_input)
        try:
            with self.tracer.start_run(
                name="pcode.turn",
                user_input=user_input,
                metadata={"workspace": str(self.tools.context.workspace_root)},
            ) as run_observation:
                try:
                    result = await self._run_turn_with_recording(
                        user_input,
                        events,
                        recorder,
                        planning_mode=planning_mode,
                        execute_plan=execute_plan,
                    )
                except Exception as exc:
                    run_observation.update(
                        output={"error": f"{type(exc).__name__}: {exc}"}
                    )
                    raise
                run_observation.update(output=result.answer)
                return result
        finally:
            self.tracer.flush()

    async def _run_turn_with_recording(
        self,
        user_input: str,
        events: TurnEvents,
        recorder: RunRecorder,
        *,
        planning_mode: bool,
        execute_plan: bool,
    ) -> PCodeTurnResult:
        self.history.append(Message("user", user_input))
        tool_statuses: list[str] = []
        active_tools = self.tools.read_only() if planning_mode else self.tools

        try:
            for index in range(self.config.max_steps):
                step_number = index + 1
                step_started_at = utc_now()
                reminders = self._reminders_for_step(
                    step_number, planning_mode=planning_mode, execute_plan=execute_plan
                )
                output, usage = await self._stream_llm_output(
                    events, active_tools, reminders
                )
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
                        with self.tracer.start_tool(
                            name=tool_name, input=tool_input
                        ) as tool_observation:
                            result = active_tools.run(tool_name, tool_input)
                            tool_observation.update(
                                output=result.output,
                                metadata={
                                    **result.metadata,
                                    "is_error": result.is_error,
                                },
                            )
                        result_text = (
                            result.output
                            if not result.is_error
                            else f"ERROR: {result.output}"
                        )
                        is_error = result.is_error

                    await events.tool_finished(tool_name, is_error)
                    tool_statuses.append(
                        f"{tool_name}: {'failed' if is_error else 'done'}"
                    )
                    self.history.append(Message("assistant", output))
                    self.history.append(Message("user", f"Observation: {result_text}"))
                    recorder.record_step(
                        llm_output=output,
                        tool_name=tool_name,
                        tool_input=tool_input,
                        observation=result_text,
                        is_error=is_error,
                        generation_usage=usage,
                        started_at=step_started_at,
                    )
                    continue

                final = FINAL_RE.search(output)
                if final:
                    answer = final.group("answer").strip()
                    await events.final_delta(answer)
                    self.history.append(Message("assistant", output))
                    recorder.record_step(
                        llm_output=output,
                        generation_usage=usage,
                        started_at=step_started_at,
                    )
                    recorder.complete(answer)
                    record_path = recorder.save()
                    return PCodeTurnResult(
                        answer=answer,
                        record_path=str(record_path),
                        tool_statuses=tool_statuses,
                    )

                if not action:
                    observation = "Invalid response. Use either Action/Action Input or Final Answer."
                    self.history.append(Message("assistant", output))
                    self.history.append(Message("user", f"Observation: {observation}"))
                    recorder.record_step(
                        llm_output=output,
                        observation=observation,
                        is_error=True,
                        generation_usage=usage,
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

    async def _stream_llm_output(
        self,
        events: TurnEvents,
        tools: ToolRegistry,
        reminders: list[str],
    ) -> tuple[str, GenerationUsage | None]:
        stable_prompt = self.system_prompt or build_stable_prompt(tools)
        model = getattr(getattr(self.client, "provider", None), "model", "unknown")
        request = ChatRequest(
            stable_prompt=stable_prompt,
            environment=build_environment_block(tools.context.workspace_root, model),
            reminders=reminders,
            messages=list(self.history),
        )
        parts: list[str] = []
        usage: GenerationUsage | None = None

        with self.tracer.start_generation(
            name="pcode.llm",
            model=getattr(getattr(self.client, "provider", None), "model", None),
            input={
                "stable_prompt": request.stable_prompt,
                "environment": request.environment,
                "reminders": request.reminders,
                "messages": [message.__dict__ for message in request.messages],
            },
            metadata={
                "message_count": len(request.messages),
                "reminder_count": len(request.reminders),
            },
        ) as generation:
            async for event in self.client.stream(request):
                if isinstance(event, TextDelta):
                    parts.append(event.text)
                    continue
                if isinstance(event, UsageDelta):
                    usage = event.usage
            output = "".join(parts)
            metadata: dict[str, object] = {}
            if usage is not None:
                metadata["usage"] = usage.__dict__
            generation.update(output=output, metadata=metadata)
            return output, usage

    def _reminders_for_step(
        self, step_number: int, *, planning_mode: bool, execute_plan: bool
    ) -> list[str]:
        reminders: list[str] = []
        if planning_mode:
            reminders.append(planning_reminder(step_number))
        elif execute_plan and step_number == 1:
            reminders.append(execute_plan_reminder())
        return reminders


def build_system_prompt(tools: ToolRegistry) -> str:
    return build_stable_prompt(tools)
