from __future__ import annotations

import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual import events
from textual.message import Message
from textual.screen import Screen
from textual.widgets import Footer, Markdown, OptionList, Static, TextArea

from codeagent.chat import ProviderStreamingClient
from codeagent.config import AgentConfig, ProviderConfig
from codeagent.llm import LLMError
from codeagent.observability import tracing_status_from_env
from codeagent.permissions import ApprovalScope, PermissionChecker, PermissionMode
from codeagent.pcode_agent import PCodeAgentSession
from codeagent.tools import build_default_registry

PlanApprovalChoice = Literal["current", "manual", "revise", "cancel"]


SNAKE_BANNER = r"""
   /\_/\
  ( o.o )
   = w =
   ____   ____          _
  |  _ \ / ___|___   __| | ___
  | |_) | |   / _ \ / _` |/ _ \
  |  __/| |__| (_) | (_| |  __/
  |_|    \____\___/ \__,_|\___|
"""


class ProviderSelectScreen(Screen[ProviderConfig]):
    def __init__(self, providers: tuple[ProviderConfig, ...]) -> None:
        super().__init__()
        self.providers = providers

    def compose(self) -> ComposeResult:
        yield Static("Select provider", id="select-title")
        yield OptionList(
            *[f"{provider.name}  ({provider.model})" for provider in self.providers],
            id="provider-list",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#provider-list", OptionList).focus()
        self.call_after_refresh(self.query_one("#provider-list", OptionList).focus)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.providers[event.option_index])


class PermissionScreen(Screen[ApprovalScope | str]):
    OPTIONS: tuple[tuple[str, ApprovalScope | str], ...] = (
        ("Allow once", "once"),
        ("Allow for session", "session"),
        ("Allow permanently", "permanent"),
        ("Deny", "deny"),
    )

    def __init__(self, tool_name: str, description: str) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.description = description

    def compose(self) -> ComposeResult:
        yield Static(f"{self.tool_name} requires permission", id="select-title")
        yield Static(self.description)
        yield OptionList(*[label for label, _scope in self.OPTIONS], id="provider-list")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#provider-list", OptionList).focus()
        self.call_after_refresh(self.query_one("#provider-list", OptionList).focus)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.OPTIONS[event.option_index][1])


class PlanApprovalScreen(Screen[PlanApprovalChoice]):
    OPTIONS: tuple[tuple[str, PlanApprovalChoice], ...] = (
        ("Execute with current permissions", "current"),
        ("Execute with manual permissions", "manual"),
        ("Revise plan", "revise"),
        ("Cancel", "cancel"),
    )

    def __init__(self, plan_path: Path, plan_content: str) -> None:
        super().__init__()
        self.plan_path = plan_path
        self.plan_content = plan_content

    def compose(self) -> ComposeResult:
        yield Static("Plan ready for approval", id="select-title")
        with VerticalScroll(id="plan-preview"):
            yield Markdown(f"Plan file: `{self.plan_path}`\n\n{self.plan_content}")
        yield OptionList(*[label for label, _choice in self.OPTIONS], id="plan-actions")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#plan-actions", OptionList).focus()
        self.call_after_refresh(self.query_one("#plan-actions", OptionList).focus)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.OPTIONS[event.option_index][1])


@dataclass
class PlanState:
    active: bool = False
    awaiting_approval: bool = False
    path: Path | None = None
    original_request: str | None = None
    approved_content: str | None = None


class ChatInput(TextArea):
    class Submitted(Message):
        def __init__(self, text_area: "ChatInput") -> None:
            self.text_area = text_area
            super().__init__()

    async def _on_key(self, event: events.Key) -> None:
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self))
            return
        if event.key in {"alt+enter", "ctrl+j"}:
            event.stop()
            event.prevent_default()
            start, end = self.selection
            self._replace_via_keyboard("\n", start, end)
            return
        await super()._on_key(event)


class TurnView:
    def __init__(self, app: "PCodeApp", user_text: str) -> None:
        self.app = app
        self.user = Static(f"> {user_text}", classes="user-message")
        self.header = Static("Imagining... (0s)", classes="assistant-header")
        self.tools = Static("", classes="tool-status")
        self.assistant = Static("", classes="assistant-message")
        self.text = ""
        self.tool_lines: list[str] = []
        self.started_at = time.monotonic()

    async def mount(self) -> None:
        await self.app.conversation.mount(self.user)
        await self.app.conversation.mount(self.header)
        await self.app.conversation.mount(self.tools)
        await self.app.conversation.mount(self.assistant)
        self.app.conversation.scroll_end(animate=False)

    async def tool_started(self, name: str) -> None:
        self.tools.update(f"Using {name}...")
        self.app.conversation.scroll_end(animate=False)

    async def tool_finished(self, name: str, is_error: bool) -> None:
        suffix = "failed" if is_error else "done"
        line = f"Using {name}... {suffix}"
        self.tool_lines.append(line)
        self.tools.update("\n".join(self.tool_lines))
        self.app.conversation.scroll_end(animate=False)

    async def final_delta(self, text: str) -> None:
        self.text += text
        self.assistant.update(self.text)
        self.app.conversation.scroll_end(animate=False)

    async def permission_requested(
        self, tool_name: str, description: str
    ) -> ApprovalScope | str:
        result = await self.app.push_screen_wait(PermissionScreen(tool_name, description))
        return result or "deny"

    async def finish(self) -> None:
        elapsed = time.monotonic() - self.started_at
        self.header.update(f"Done in {elapsed:.1f}s")
        await self.assistant.remove()
        self.assistant = Markdown(self.text, classes="assistant-message")
        await self.app.conversation.mount(self.assistant)
        self.app.conversation.scroll_end(animate=False)

    async def fail(self, error: str) -> None:
        elapsed = time.monotonic() - self.started_at
        self.header.update(f"Failed after {elapsed:.1f}s")
        self.assistant.add_class("error-message")
        self.assistant.update(error)
        self.app.conversation.scroll_end(animate=False)

    def tick(self) -> None:
        elapsed = int(time.monotonic() - self.started_at)
        self.header.update(f"Imagining... ({elapsed}s)")


class PCodeApp(App[None]):
    CSS = """
    Screen { layout: vertical; }
    #banner { padding: 1 2; border-bottom: solid $primary; }
    #ready { padding: 0 2; color: $text-muted; }
    #conversation { height: 1fr; padding: 1 2; }
    .user-message { color: $accent; margin-top: 1; }
    .assistant-header { color: $text-muted; margin-top: 1; }
    .tool-status { color: $warning; }
    .assistant-message { color: $text; }
    .error-message { color: $error; }
    #input { height: 5; border: solid $primary; }
    #status { height: 1; color: $text-muted; padding: 0 1; }
    #select-title { padding: 1 2; text-style: bold; }
    #provider-list { height: 1fr; }
    #plan-preview { height: 1fr; padding: 0 2; }
    #plan-actions { height: 6; border-top: solid $primary; }
    """
    BINDINGS = [("ctrl+c", "quit", "Quit")]

    def __init__(
        self,
        providers: tuple[ProviderConfig, ...],
        agent_config: AgentConfig,
        workspace: Path,
    ) -> None:
        super().__init__()
        self.providers = providers
        self.agent_config = agent_config
        self.workspace = workspace
        self.provider: ProviderConfig | None = None
        self.session: PCodeAgentSession | None = None
        self.conversation: VerticalScroll
        self.input_box: ChatInput
        self.current_turn: TurnView | None = None
        self.busy = False
        self.planning_mode = False
        self.plan_state = PlanState()

    def compose(self) -> ComposeResult:
        yield Static(self._banner_text(), id="banner")
        yield Static("Ready. Type /exit to quit.", id="ready")
        self.conversation = VerticalScroll(id="conversation")
        yield self.conversation
        self.input_box = ChatInput("", id="input")
        self.input_box.placeholder = "Send a message..."
        yield self.input_box
        yield Horizontal(
            Static("", id="provider-status"),
            Static("", id="model-status"),
            Static("mode: do", id="mode-status"),
            Static(tracing_status_from_env(), id="trace-status"),
            id="status",
        )

    async def on_mount(self) -> None:
        if len(self.providers) == 1:
            await self._select_provider(self.providers[0])
        else:
            provider = await self.push_screen_wait(ProviderSelectScreen(self.providers))
            await self._select_provider(provider)
        self.set_interval(1, self._tick)
        self.input_box.focus()

    async def _select_provider(self, provider: ProviderConfig) -> None:
        self.provider = provider
        permission_checker = PermissionChecker.for_workspace(
            self.workspace, mode=PermissionMode(self.agent_config.permission_mode)
        )
        registry = build_default_registry(
            self.workspace,
            output_limit=self.agent_config.tool_output_limit,
            permission_checker=permission_checker,
        )
        self.session = PCodeAgentSession(
            client=ProviderStreamingClient(provider),
            tools=registry,
            config=self.agent_config,
        )
        self.query_one("#provider-status", Static).update(
            f"{provider.name} | {provider.model}"
        )
        self.query_one("#model-status", Static).update("")

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        if event.text_area is self.input_box:
            await self._submit()

    async def _submit(self) -> None:
        if self.busy or self.session is None:
            return
        text = self.input_box.text.strip()
        if not text:
            return
        if text == "/exit":
            self.exit()
            return
        if text == "/plan-cancel":
            self.input_box.text = ""
            self._cancel_plan_mode()
            self.query_one("#ready", Static).update("Plan cancelled.")
            self._refocus_input()
            return
        if text == "/plan":
            self.input_box.text = ""
            self._start_plan_mode(original_request=None, clear_plan=False)
            self._update_mode_status()
            self.query_one("#ready", Static).update(
                "Planning mode. Send the request you want planned."
            )
            self._refocus_input()
            return
        if text.startswith("/plan "):
            text = text[6:].strip()
            if not text:
                return
            self._start_plan_mode(original_request=text, clear_plan=True)
            self._update_mode_status()

        execute_plan = False
        if self.plan_state.awaiting_approval:
            if text == "/do":
                self.input_box.text = ""
                await self._execute_approved_plan(permission_mode=None)
                return
            if text in {"/do --manual", "/do manual"}:
                self.input_box.text = ""
                await self._execute_approved_plan(
                    permission_mode=self._manual_execution_mode()
                )
                return
            if text.startswith("/do "):
                self.query_one("#ready", Static).update(
                    "Plan is waiting for approval. Use /do, /do --manual, or type revision feedback."
                )
                self.input_box.text = ""
                self._refocus_input()
                return
            self.planning_mode = True
            self.plan_state.awaiting_approval = False
            self._update_mode_status()
        elif text == "/do":
            self.input_box.text = ""
            if self.planning_mode and self._plan_file_has_content():
                assert self.plan_state.path is not None
                self.planning_mode = False
                self._update_mode_status()
                await self._show_plan_approval(self.plan_state.path)
                return
            self._cancel_plan_mode()
            self._update_mode_status()
            self.query_one("#ready", Static).update(
                "Execution mode. Type /plan to plan first."
            )
            self._refocus_input()
            return
        if text.startswith("/do "):
            text = text[4:].strip()
            if not text:
                return
            self.planning_mode = False
            execute_plan = True
            self._update_mode_status()

        if self.planning_mode and self.plan_state.original_request is None:
            self.plan_state.original_request = text

        self.input_box.text = ""
        self.input_box.disabled = True
        self.busy = True
        turn = TurnView(self, text)
        self.current_turn = turn
        await turn.mount()
        self.run_worker(
            self._run_turn(text, turn, execute_plan=execute_plan),
            name="pcode-turn",
            group="pcode-turn",
            exit_on_error=False,
        )

    async def _run_turn(
        self, text: str, turn: TurnView, *, execute_plan: bool = False
    ) -> None:
        try:
            assert self.session is not None
            result = await self.session.run_turn(
                text,
                turn,
                planning_mode=self.planning_mode,
                execute_plan=execute_plan,
                plan_path=self.plan_state.path,
                approved_plan=self.plan_state.approved_content if execute_plan else None,
                original_request=self.plan_state.original_request if execute_plan else None,
            )
        except LLMError as exc:
            await turn.fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - keep the TUI session recoverable.
            await turn.fail(f"{type(exc).__name__}: {exc}")
        else:
            await turn.finish()
            if result is not None and result.plan_ready and result.plan_path:
                self.planning_mode = False
                self.plan_state.active = True
                self.plan_state.awaiting_approval = True
                self.plan_state.path = Path(result.plan_path)
                self._update_mode_status()
                self.query_one("#ready", Static).update(
                    "Plan ready. Type /do to execute, /do --manual for manual permissions, or type feedback to revise."
                )
            elif execute_plan:
                self.plan_state = PlanState()
        finally:
            if self.current_turn is turn:
                self.busy = False
                self.input_box.disabled = False
                self.input_box.focus()
                self.current_turn = None

    def _tick(self) -> None:
        if self.current_turn is not None:
            self.current_turn.tick()

    def _refocus_input(self) -> None:
        self.input_box.disabled = False
        self.input_box.focus()
        self.call_after_refresh(self.input_box.focus)

    def _update_mode_status(self) -> None:
        if self.plan_state.awaiting_approval:
            mode = "plan-review"
        else:
            mode = "plan" if self.planning_mode else "do"
        self.query_one("#mode-status", Static).update(f"mode: {mode}")

    def _start_plan_mode(
        self, *, original_request: str | None, clear_plan: bool
    ) -> None:
        plan_path = self.workspace / ".codeagent" / "plans" / "current.md"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        if clear_plan and plan_path.exists():
            plan_path.unlink()
        self.planning_mode = True
        self.plan_state = PlanState(
            active=True,
            path=plan_path,
            original_request=original_request,
        )

    def _cancel_plan_mode(self) -> None:
        self.planning_mode = False
        self.plan_state = PlanState()
        self._update_mode_status()

    def _plan_file_has_content(self) -> bool:
        if self.plan_state.path is None:
            return False
        try:
            return self.plan_state.path.exists() and bool(
                self.plan_state.path.read_text(encoding="utf-8").strip()
            )
        except OSError:
            return False

    async def _show_plan_approval(self, plan_path: Path) -> None:
        try:
            plan_content = plan_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            self.query_one("#ready", Static).update(f"Could not read plan: {exc}")
            return
        choice = await self.push_screen_wait(PlanApprovalScreen(plan_path, plan_content))
        if choice == "revise":
            self.planning_mode = True
            self.plan_state.awaiting_approval = False
            self.plan_state.active = True
            self.plan_state.path = plan_path
            self._update_mode_status()
            self.query_one("#ready", Static).update("Planning mode. Type revision feedback.")
            self.input_box.disabled = False
            self.input_box.focus()
            return
        if choice == "cancel" or choice is None:
            self._cancel_plan_mode()
            self.query_one("#ready", Static).update("Plan cancelled.")
            self.input_box.disabled = False
            self.input_box.focus()
            return

        mode = self._manual_execution_mode() if choice == "manual" else None
        await self._execute_approved_plan(
            plan_content=plan_content,
            permission_mode=mode,
        )

    async def _execute_approved_plan(
        self,
        *,
        plan_content: str | None = None,
        permission_mode: PermissionMode | None,
    ) -> None:
        if self.plan_state.path is None:
            self.query_one("#ready", Static).update("No plan file to execute.")
            return
        if plan_content is None:
            try:
                plan_content = self.plan_state.path.read_text(encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                self.query_one("#ready", Static).update(f"Could not read plan: {exc}")
                return
        self.plan_state.approved_content = plan_content
        self.plan_state.awaiting_approval = False
        self.planning_mode = False
        self._update_mode_status()
        self.query_one("#ready", Static).update("Executing approved plan.")
        self.input_box.disabled = True
        self.busy = True
        turn = TurnView(self, "Execute approved plan")
        self.current_turn = turn
        await turn.mount()
        self.run_worker(
            self._run_turn_with_permission_mode(
                "Execute approved plan", turn, permission_mode=permission_mode
            ),
            name="pcode-turn",
            group="pcode-turn",
            exit_on_error=False,
        )

    async def _run_turn_with_permission_mode(
        self,
        text: str,
        turn: TurnView,
        *,
        permission_mode: PermissionMode | None,
    ) -> None:
        checker = self.session.tools.permission_checker if self.session else None
        original_mode = checker.mode if checker is not None else None
        if checker is not None and permission_mode is not None:
            checker.mode = permission_mode
        try:
            await self._run_turn(text, turn, execute_plan=True)
        finally:
            if checker is not None and original_mode is not None:
                checker.mode = original_mode

    def _manual_execution_mode(self) -> PermissionMode:
        current = PermissionMode(self.agent_config.permission_mode)
        if current in {
            PermissionMode.ACCEPT_EDITS,
            PermissionMode.BYPASS,
            PermissionMode.DONT_ASK,
        }:
            return PermissionMode.DEFAULT
        return current

    def _banner_text(self) -> str:
        return f"{SNAKE_BANNER}\nPCode {package_version()}    {self.workspace}"


def package_version() -> str:
    try:
        return version("codeagent")
    except PackageNotFoundError:
        return "0.1.0"
