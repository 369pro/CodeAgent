from __future__ import annotations

from dataclasses import dataclass, field
import json
import re

from codeagent.config import AgentConfig
from codeagent.llm import ChatClient, Message
from codeagent.tools import ToolRegistry


ACTION_RE = re.compile(r"Action:\s*(?P<name>[A-Za-z_][\w-]*)\s*\nAction Input:\s*(?P<input>\{.*\})", re.DOTALL)
FINAL_RE = re.compile(r"Final Answer:\s*(?P<answer>.*)", re.DOTALL)


@dataclass(frozen=True)
class Step:
    llm_output: str
    tool_name: str | None = None
    tool_input: dict[str, object] | None = None
    observation: str | None = None


@dataclass(frozen=True)
class RunResult:
    answer: str
    steps: list[Step] = field(default_factory=list)


class ReActAgent:
    def __init__(self, llm: ChatClient, tools: ToolRegistry, config: AgentConfig | None = None) -> None:
        self.llm = llm
        self.tools = tools
        self.config = config or AgentConfig()

    def run(self, user_input: str) -> RunResult:
        messages = [
            Message("system", self._system_prompt()),
            Message("user", user_input),
        ]
        steps: list[Step] = []

        for _ in range(self.config.max_steps):
            output = self.llm.complete(messages)
            final = FINAL_RE.search(output)
            if final:
                steps.append(Step(llm_output=output))
                return RunResult(answer=final.group("answer").strip(), steps=steps)

            action = ACTION_RE.search(output)
            if not action:
                observation = "Invalid response. Use either Action/Action Input or Final Answer."
                steps.append(Step(llm_output=output, observation=observation))
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
            else:
                result = self.tools.run(tool_name, tool_input)
                result_text = result.output if result.ok else f"ERROR: {result.output}"

            if len(result_text) > self.config.tool_output_limit:
                result_text = result_text[: self.config.tool_output_limit] + "\n...[truncated]"

            steps.append(
                Step(
                    llm_output=output,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    observation=result_text,
                )
            )
            messages.append(Message("assistant", output))
            messages.append(Message("user", f"Observation: {result_text}"))

        raise RuntimeError(f"ReAct loop exceeded max_steps={self.config.max_steps}.")

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
