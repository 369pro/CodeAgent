from __future__ import annotations

from dataclasses import dataclass, field
import html
import json
from pathlib import Path
import re
from typing import Literal, Protocol
import xml.etree.ElementTree as ET

from codeagent.chat import ChatRequest, StreamingChatClient, TextDelta, UsageDelta
from codeagent.config import AgentConfig
from codeagent.hooks import HookEventContext, HookManager
from codeagent.hooks.manager import tool_subject
from codeagent.llm import LLMError, Message
from codeagent.observability import Tracer, build_tracer_from_env
from codeagent.permissions import ApprovalScope
from codeagent.prompts import (
    GenerationUsage,
    build_environment_block,
    build_plan_mode_reminder,
    build_stable_prompt,
    execute_plan_reminder,
)
from codeagent.records import RunRecorder, utc_now
from codeagent.tools import ToolRegistry, ToolResult


ACTION_RE = re.compile(
    r"Action:\s*(?P<name>[A-Za-z_][\w-]*)\s+Action Input:\s*(?P<input>\{.*\})",
    re.DOTALL,
)
XML_TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(?P<body>.*?)\s*</tool_call>", re.DOTALL | re.IGNORECASE
)
DSML_MARK = r"(?:[|｜]\s*){1,2}DSML\s*(?:[|｜]\s*){1,2}"
DSML_TOOL_CALL_RE = re.compile(
    rf"<\s*{DSML_MARK}\s*tool_calls\s*>(?P<body>.*?)</\s*{DSML_MARK}\s*tool_calls\s*>",
    re.DOTALL | re.IGNORECASE,
)
DSML_INVOKE_RE = re.compile(
    rf"<\s*{DSML_MARK}\s*invoke\s+name=\"(?P<name>[^\"]+)\"\s*>"
    rf"(?P<body>.*?)</\s*{DSML_MARK}\s*invoke\s*>",
    re.DOTALL | re.IGNORECASE,
)
DSML_PARAMETER_RE = re.compile(
    rf"<\s*{DSML_MARK}\s*parameter\s+name=\"(?P<name>[^\"]+)\"(?:\s+string=\"true\")?\s*>"
    rf"(?P<value>.*?)</\s*{DSML_MARK}\s*parameter\s*>",
    re.DOTALL | re.IGNORECASE,
)
NAMED_XML_TOOL_RE = re.compile(
    r"<(?P<name>[A-Za-z_][\w-]*)\b[^>]*>.*?</(?P=name)>",
    re.DOTALL,
)
FINAL_RE = re.compile(r"Final Answer:\s*(?P<answer>.*)", re.DOTALL)
TOOL_FAILURE_CIRCUIT_THRESHOLD = 3


class TurnEvents(Protocol):
    async def tool_started(self, name: str) -> None: ...

    async def tool_finished(self, name: str, is_error: bool) -> None: ...

    async def final_delta(self, text: str) -> None: ...

    async def permission_requested(
        self, tool_name: str, description: str
    ) -> ApprovalScope | Literal["deny"]: ...


@dataclass
class PCodeTurnResult:
    answer: str
    record_path: str | None = None
    tool_statuses: list[str] = field(default_factory=list)
    plan_ready: bool = False
    plan_path: str | None = None


@dataclass(frozen=True)
class ParsedAction:
    name: str
    tool_input: dict[str, object]
    input_error: str | None = None


@dataclass(frozen=True)
class TurnContext:
    model: str
    planning_mode: bool
    execute_plan: bool
    active_tools: ToolRegistry
    plan_path: Path | None = None
    approved_plan: str | None = None
    original_request: str | None = None


@dataclass(frozen=True)
class ModelStep:
    started_at: str
    output: str
    usage: GenerationUsage | None


@dataclass(frozen=True)
class ToolCallOutcome:
    result_text: str
    is_error: bool


@dataclass
class ToolFailureTracker:
    tool_name: str | None = None
    count: int = 0

    def blocks(self, tool_name: str) -> bool:
        return (
            self.tool_name == tool_name
            and self.count >= TOOL_FAILURE_CIRCUIT_THRESHOLD
        )

    def record(self, tool_name: str, is_error: bool) -> str | None:
        if not is_error:
            self.tool_name = None
            self.count = 0
            return None

        if self.tool_name == tool_name:
            self.count += 1
        else:
            self.tool_name = tool_name
            self.count = 1

        if self.count != TOOL_FAILURE_CIRCUIT_THRESHOLD:
            return None
        return (
            "\nTool circuit breaker opened for "
            f"{tool_name}: it failed {TOOL_FAILURE_CIRCUIT_THRESHOLD} "
            "consecutive times. Use a different tool or change arguments."
        )


class PCodeAgentSession:
    def __init__(
        self,
        client: StreamingChatClient,
        tools: ToolRegistry,
        config: AgentConfig | None = None,
        system_prompt: str | None = None,
        tracer: Tracer | None = None,
        hook_manager: HookManager | None = None,
    ) -> None:
        self.client = client
        self.tools = tools
        self.config = config or AgentConfig()
        self.system_prompt = system_prompt
        self.history: list[Message] = []
        self.tracer = tracer or build_tracer_from_env()
        self.hook_manager = hook_manager or HookManager.load_for_workspace(
            self.tools.context.workspace_root
        )

    async def run_turn(
        self,
        user_input: str,
        events: TurnEvents,
        *,
        planning_mode: bool = False,
        execute_plan: bool = False,
        plan_path: str | Path | None = None,
        approved_plan: str | None = None,
        original_request: str | None = None,
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
                        plan_path=plan_path,
                        approved_plan=approved_plan,
                        original_request=original_request,
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
        plan_path: str | Path | None,
        approved_plan: str | None,
        original_request: str | None,
    ) -> PCodeTurnResult:
        context = self._build_turn_context(
            planning_mode=planning_mode,
            execute_plan=execute_plan,
            plan_path=plan_path,
            approved_plan=approved_plan,
            original_request=original_request,
        )
        await self._fire_turn_hook("turn.start", context, recorder)
        self.history.append(Message("user", user_input))
        tool_statuses: list[str] = []
        failure_tracker = ToolFailureTracker()

        try:
            for index in range(self.config.max_steps):
                step = await self._run_model_step(
                    index + 1, events, context, recorder
                )
                action = parse_action(step.output)

                if action is not None:
                    result = await self._handle_action_step(
                        action,
                        step,
                        events,
                        recorder,
                        context,
                        tool_statuses,
                        failure_tracker,
                    )
                    if result is not None:
                        return result
                    continue

                final_answer = _extract_final_answer(step.output)
                if final_answer is not None:
                    plan_ready = (
                        context.planning_mode
                        and context.plan_path is not None
                        and self._plan_file_has_content(context.plan_path)
                    )
                    return await self._complete_turn(
                        final_answer,
                        events,
                        recorder,
                        context,
                        tool_statuses,
                        step=step,
                        plan_ready=plan_ready,
                    )

                fallback_answer = step.output.strip()
                if tool_statuses and fallback_answer:
                    return await self._complete_turn(
                        fallback_answer,
                        events,
                        recorder,
                        context,
                        tool_statuses,
                        step=step,
                    )
                self._record_invalid_model_response(step, recorder)

            error = f"ReAct loop exceeded max_steps={self.config.max_steps}."
            recorder.fail(error, status="max_steps_exceeded")
            await self._fire_turn_hook("turn.end", context, recorder)
            recorder.save()
            raise LLMError(error)
        except Exception as exc:
            if recorder.record.status == "running":
                recorder.fail(f"{type(exc).__name__}: {exc}")
                recorder.save()
            raise

    def _build_turn_context(
        self,
        *,
        planning_mode: bool,
        execute_plan: bool,
        plan_path: str | Path | None,
        approved_plan: str | None,
        original_request: str | None,
    ) -> TurnContext:
        model = getattr(getattr(self.client, "provider", None), "model", "unknown")
        active_plan_path = (
            self._prepare_plan_path(plan_path) if planning_mode else None
        )
        active_tools = (
            self.tools.plan_mode(active_plan_path)
            if active_plan_path is not None
            else self.tools
        )
        return TurnContext(
            model=model,
            planning_mode=planning_mode,
            execute_plan=execute_plan,
            active_tools=active_tools,
            plan_path=active_plan_path,
            approved_plan=approved_plan,
            original_request=original_request,
        )

    async def _run_model_step(
        self,
        step_number: int,
        events: TurnEvents,
        context: TurnContext,
        recorder: RunRecorder,
    ) -> ModelStep:
        started_at = utc_now()
        reminders = self._reminders_for_step(step_number, context)
        hook_reminders, hook_results = await self.hook_manager.collect_reminders(
            HookEventContext(
                "message.before_model",
                self.tools.context.workspace_root,
                planning_mode=context.planning_mode,
                model=context.model,
            )
        )
        reminders.extend(hook_reminders)
        recorder.record_hook_results(hook_results)

        output, usage = await self._stream_llm_output(
            events, context.active_tools, reminders
        )
        recorder.record_hook_results(
            await self.hook_manager.fire(
                HookEventContext(
                    "message.after_model",
                    self.tools.context.workspace_root,
                    planning_mode=context.planning_mode,
                    model=context.model,
                )
            )
        )
        return ModelStep(started_at, output, usage)

    async def _handle_action_step(
        self,
        action: ParsedAction,
        step: ModelStep,
        events: TurnEvents,
        recorder: RunRecorder,
        context: TurnContext,
        tool_statuses: list[str],
        failure_tracker: ToolFailureTracker,
    ) -> PCodeTurnResult | None:
        tool_name = action.name
        tool_input = action.tool_input
        await events.tool_started(tool_name)

        if action.input_error is not None:
            outcome = ToolCallOutcome(
                f"Invalid Action Input: {action.input_error}", True
            )
        else:
            outcome = await self._execute_tool_action(
                tool_name, tool_input, events, recorder, context, failure_tracker
            )

        await events.tool_finished(tool_name, outcome.is_error)
        tool_statuses.append(
            f"{tool_name}: {'failed' if outcome.is_error else 'done'}"
        )
        self._record_tool_observation(action, step, outcome, recorder)

        if (
            context.planning_mode
            and tool_name == "exit_plan_mode"
            and not outcome.is_error
            and context.plan_path is not None
        ):
            return await self._complete_turn(
                "Plan ready for approval.",
                events,
                recorder,
                context,
                tool_statuses,
                plan_ready=True,
                emit_final=False,
            )
        return None

    async def _execute_tool_action(
        self,
        tool_name: str,
        tool_input: dict[str, object],
        events: TurnEvents,
        recorder: RunRecorder,
        context: TurnContext,
        failure_tracker: ToolFailureTracker,
    ) -> ToolCallOutcome:
        result = await self._prechecked_tool_result(
            tool_name, tool_input, events, recorder, context, failure_tracker
        )

        with self.tracer.start_tool(
            name=tool_name, input=tool_input
        ) as tool_observation:
            if result is None:
                result = context.active_tools.run_prechecked(tool_name, tool_input)
            tool_observation.update(
                output=result.output,
                metadata={**result.metadata, "is_error": result.is_error},
            )

        await self._fire_tool_after_hook(
            tool_name, tool_input, result, recorder, context
        )
        result_text = (
            result.output if not result.is_error else f"ERROR: {result.output}"
        )
        circuit_message = failure_tracker.record(tool_name, result.is_error)
        if circuit_message is not None:
            result_text += circuit_message
        return ToolCallOutcome(result_text, result.is_error)

    async def _prechecked_tool_result(
        self,
        tool_name: str,
        tool_input: dict[str, object],
        events: TurnEvents,
        recorder: RunRecorder,
        context: TurnContext,
        failure_tracker: ToolFailureTracker,
    ) -> ToolResult | None:
        if failure_tracker.blocks(tool_name):
            return ToolResult(
                (
                    "Tool circuit breaker open: "
                    f"{tool_name} failed {failure_tracker.count} consecutive times. "
                    "Use a different tool or change arguments before retrying."
                ),
                is_error=True,
                metadata={"circuit_breaker": True, "tool": tool_name},
            )

        tool = context.active_tools.get(tool_name)
        checker = context.active_tools.permission_checker
        if tool is not None and checker is not None:
            hard_decision = checker.check_hard_safety(tool, tool_input)
            if hard_decision.effect == "deny":
                return ToolResult(
                    f"Permission denied: {hard_decision.reason}",
                    is_error=True,
                    metadata={"permission": hard_decision.reason},
                )

        before = await self.hook_manager.fire_tool_before(
            self._tool_hook_context("tool.before", tool_name, tool_input, context)
        )
        recorder.record_hook_results(before.results)
        if before.blocked:
            return ToolResult(
                f"Hook blocked tool call: {before.reason}",
                is_error=True,
                metadata={"hook_blocked": True},
            )

        if tool is None or checker is None:
            return None

        decision = checker.check_policy(tool, tool_input)
        if decision.effect == "deny":
            return ToolResult(
                f"Permission denied: {decision.reason}",
                is_error=True,
                metadata={"permission": decision.reason},
            )
        if decision.effect != "ask":
            return None

        approval_scope = await events.permission_requested(
            tool_name,
            _permission_description(
                tool_name, decision.normalized_content or decision.content
            ),
        )
        if approval_scope in {"once", "session", "permanent"}:
            checker.approve(
                tool.name,
                decision.normalized_content or decision.content,
                approval_scope,
            )
            return None
        return ToolResult(
            f"Permission denied: {decision.reason}",
            is_error=True,
            metadata={"permission": decision.reason},
        )

    async def _fire_tool_after_hook(
        self,
        tool_name: str,
        tool_input: dict[str, object],
        result: ToolResult,
        recorder: RunRecorder,
        context: TurnContext,
    ) -> None:
        if any(
            result.metadata.get(key)
            for key in ("hook_blocked", "permission", "circuit_breaker")
        ):
            return
        event_name = "tool.error" if result.is_error else "tool.after"
        recorder.record_hook_results(
            await self.hook_manager.fire(
                self._tool_hook_context(event_name, tool_name, tool_input, context)
            )
        )

    def _tool_hook_context(
        self,
        event: str,
        tool_name: str,
        tool_input: dict[str, object],
        context: TurnContext,
    ) -> HookEventContext:
        return HookEventContext(
            event,
            self.tools.context.workspace_root,
            planning_mode=context.planning_mode,
            model=context.model,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_subject=tool_subject(tool_name, tool_input),
        )

    def _record_tool_observation(
        self,
        action: ParsedAction,
        step: ModelStep,
        outcome: ToolCallOutcome,
        recorder: RunRecorder,
    ) -> None:
        self.history.append(Message("assistant", step.output))
        self.history.append(Message("user", f"Observation: {outcome.result_text}"))
        recorder.record_step(
            llm_output=step.output,
            tool_name=action.name,
            tool_input=action.tool_input,
            observation=outcome.result_text,
            is_error=outcome.is_error,
            generation_usage=step.usage,
            started_at=step.started_at,
        )

    def _record_invalid_model_response(
        self, step: ModelStep, recorder: RunRecorder
    ) -> None:
        observation = (
            "Invalid response. Use either Action/Action Input or Final Answer."
        )
        self.history.append(Message("assistant", step.output))
        self.history.append(Message("user", f"Observation: {observation}"))
        recorder.record_step(
            llm_output=step.output,
            observation=observation,
            is_error=True,
            generation_usage=step.usage,
            started_at=step.started_at,
        )

    async def _complete_turn(
        self,
        answer: str,
        events: TurnEvents,
        recorder: RunRecorder,
        context: TurnContext,
        tool_statuses: list[str],
        *,
        step: ModelStep | None = None,
        plan_ready: bool = False,
        emit_final: bool = True,
    ) -> PCodeTurnResult:
        if emit_final:
            await events.final_delta(answer)

        if step is not None:
            self.history.append(Message("assistant", step.output))
            recorder.record_step(
                llm_output=step.output,
                generation_usage=step.usage,
                started_at=step.started_at,
            )

        recorder.complete(answer)
        await self._fire_turn_hook("turn.end", context, recorder)
        record_path = recorder.save()
        return PCodeTurnResult(
            answer=answer,
            record_path=str(record_path),
            tool_statuses=tool_statuses,
            plan_ready=plan_ready,
            plan_path=(
                str(context.plan_path) if plan_ready and context.plan_path else None
            ),
        )

    async def _fire_turn_hook(
        self, event: str, context: TurnContext, recorder: RunRecorder
    ) -> None:
        recorder.record_hook_results(
            await self.hook_manager.fire(
                HookEventContext(
                    event,
                    self.tools.context.workspace_root,
                    planning_mode=context.planning_mode,
                    model=context.model,
                )
            )
        )

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
        self,
        step_number: int,
        context: TurnContext,
    ) -> list[str]:
        reminders: list[str] = []
        if context.planning_mode:
            assert context.plan_path is not None
            reminders.append(
                build_plan_mode_reminder(
                    str(context.plan_path),
                    context.plan_path.exists()
                    and bool(context.plan_path.read_text(encoding="utf-8").strip()),
                    step_number,
                )
            )
        elif context.execute_plan and step_number == 1:
            reminders.append(
                execute_plan_reminder(context.approved_plan, context.original_request)
            )
        return reminders

    def _prepare_plan_path(self, plan_path: str | Path | None) -> Path:
        path = (
            Path(plan_path)
            if plan_path is not None
            else self.tools.context.workspace_root
            / ".codeagent"
            / "plans"
            / "current.md"
        )
        if not path.is_absolute():
            path = self.tools.context.workspace_root / path
        resolved = path.resolve(strict=False)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    def _plan_file_has_content(self, plan_path: Path) -> bool:
        try:
            return plan_path.exists() and bool(
                plan_path.read_text(encoding="utf-8").strip()
            )
        except OSError:
            return False


def build_system_prompt(tools: ToolRegistry) -> str:
    return build_stable_prompt(tools)


def _extract_final_answer(output: str) -> str | None:
    final = FINAL_RE.search(output)
    if final is None:
        return None
    return final.group("answer").strip()


def parse_action(output: str) -> ParsedAction | None:
    action = ACTION_RE.search(output)
    if action:
        try:
            tool_input = json.loads(action.group("input"))
            if not isinstance(tool_input, dict):
                raise ValueError("Action Input must be a JSON object.")
        except Exception as exc:  # noqa: BLE001 - invalid model output becomes an observation.
            return ParsedAction(action.group("name"), {}, str(exc))
        return ParsedAction(action.group("name"), tool_input)

    dsml_call = _parse_dsml_tool_call(output)
    if dsml_call is not None:
        return dsml_call

    xml_call = _parse_xml_tool_call(output)
    if xml_call is not None:
        return xml_call

    return _parse_named_xml_tool_call(output)


def _parse_xml_tool_call(output: str) -> ParsedAction | None:
    xml_call = XML_TOOL_CALL_RE.search(output)
    if xml_call is None:
        return None
    body = xml_call.group("body").strip()
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return None
    name = lines[0]
    if not re.match(r"^[A-Za-z_][\w-]*$", name):
        return ParsedAction(name, {}, f"Invalid tool name: {name}")
    if len(lines) == 1:
        return ParsedAction(name, {})
    if len(lines) % 2 == 0:
        return ParsedAction(
            name, {}, "XML tool call arguments must be key/value line pairs."
        )
    tool_input = {lines[index]: lines[index + 1] for index in range(1, len(lines), 2)}
    return ParsedAction(name, tool_input)


def _parse_dsml_tool_call(output: str) -> ParsedAction | None:
    tool_call = DSML_TOOL_CALL_RE.search(output)
    if tool_call is None:
        return None
    invoke = DSML_INVOKE_RE.search(tool_call.group("body"))
    if invoke is None:
        return ParsedAction("", {}, "DSML tool call is missing invoke.")
    name = html.unescape(invoke.group("name")).strip()
    if not re.match(r"^[A-Za-z_][\w-]*$", name):
        return ParsedAction(name, {}, f"Invalid tool name: {name}")
    tool_input: dict[str, object] = {}
    for parameter in DSML_PARAMETER_RE.finditer(invoke.group("body")):
        parameter_name = html.unescape(parameter.group("name")).strip()
        if not parameter_name:
            return ParsedAction(name, {}, "DSML parameter is missing a name.")
        tool_input[parameter_name] = html.unescape(parameter.group("value").strip())
    return ParsedAction(name, tool_input)


def _parse_named_xml_tool_call(output: str) -> ParsedAction | None:
    for match in NAMED_XML_TOOL_RE.finditer(output):
        raw_xml = match.group(0)
        try:
            element = ET.fromstring(raw_xml)
        except ET.ParseError:
            continue
        name = element.tag.strip()
        if not re.match(r"^[A-Za-z_][\w-]*$", name):
            return ParsedAction(name, {}, f"Invalid tool name: {name}")
        if len(element) == 0:
            return ParsedAction(name, {})
        tool_input: dict[str, object] = {}
        for child in element:
            parameter_name = child.tag.strip()
            if not re.match(r"^[A-Za-z_][\w-]*$", parameter_name):
                return ParsedAction(
                    name, {}, f"Invalid XML parameter name: {parameter_name}"
                )
            tool_input[parameter_name] = "".join(child.itertext()).strip()
        return ParsedAction(name, tool_input)
    return None


def _permission_description(tool_name: str, content: str) -> str:
    return f"{tool_name} wants to run: {content}" if content else f"{tool_name} requires approval."
