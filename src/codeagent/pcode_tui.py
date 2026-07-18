from __future__ import annotations

import asyncio
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

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
from codeagent.pcode_agent import PCodeAgentSession
from codeagent.tools import build_default_registry


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

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(self.providers[event.option_index])


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
        registry = build_default_registry(
            self.workspace, output_limit=self.agent_config.tool_output_limit
        )
        self.session = PCodeAgentSession(
            client=ProviderStreamingClient(provider),
            tools=registry,
            config=self.agent_config,
        )
        self.query_one("#provider-status", Static).update(provider.name)
        self.query_one("#model-status", Static).update(provider.model)

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
        if text == "/plan":
            self.input_box.text = ""
            self.planning_mode = True
            self._update_mode_status()
            self.query_one("#ready", Static).update(
                "Planning mode. Type /do to execute."
            )
            return

        execute_plan = False
        if text == "/do":
            self.input_box.text = ""
            self.planning_mode = False
            self._update_mode_status()
            self.query_one("#ready", Static).update(
                "Execution mode. Type /plan to plan first."
            )
            return
        if text.startswith("/do "):
            text = text[4:].strip()
            if not text:
                return
            self.planning_mode = False
            execute_plan = True
            self._update_mode_status()

        self.input_box.text = ""
        self.input_box.disabled = True
        self.busy = True
        turn = TurnView(self, text)
        self.current_turn = turn
        await turn.mount()
        asyncio.create_task(self._run_turn(text, turn, execute_plan=execute_plan))

    async def _run_turn(
        self, text: str, turn: TurnView, *, execute_plan: bool = False
    ) -> None:
        try:
            assert self.session is not None
            await self.session.run_turn(
                text, turn, planning_mode=self.planning_mode, execute_plan=execute_plan
            )
        except LLMError as exc:
            await turn.fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - keep the TUI session recoverable.
            await turn.fail(f"{type(exc).__name__}: {exc}")
        else:
            await turn.finish()
        finally:
            self.busy = False
            self.input_box.disabled = False
            self.input_box.focus()
            self.current_turn = None

    def _tick(self) -> None:
        if self.current_turn is not None:
            self.current_turn.tick()

    def _update_mode_status(self) -> None:
        mode = "plan" if self.planning_mode else "do"
        self.query_one("#mode-status", Static).update(f"mode: {mode}")

    def _banner_text(self) -> str:
        return f"{SNAKE_BANNER}\nPCode {package_version()}    {self.workspace}"


def package_version() -> str:
    try:
        return version("codeagent")
    except PackageNotFoundError:
        return "0.1.0"
