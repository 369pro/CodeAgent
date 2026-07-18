from __future__ import annotations

from pathlib import Path

from codeagent.tools.base import Tool, ToolContext, ToolResult, truncate_output
from codeagent.tools.bash import Bash
from codeagent.tools.edit_file import EditFile
from codeagent.tools.file_state import FileStateCache
from codeagent.tools.find_file import FindFile
from codeagent.tools.git_diff import GitDiff
from codeagent.tools.git_status import GitStatus
from codeagent.tools.glob import Glob
from codeagent.tools.grep import Grep
from codeagent.tools.read_file import ReadFile
from codeagent.tools.write_file import WriteFile


class ToolRegistry:
    def __init__(self, context: ToolContext) -> None:
        self.context = context
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def read_only(self) -> "ToolRegistry":
        registry = ToolRegistry(self.context)
        for name in self.names():
            tool = self._tools[name]
            if tool.category == "read":
                registry.register(tool)
        return registry

    def descriptions(self) -> str:
        lines: list[str] = []
        for tool in self._tools.values():
            lines.append(f"- {tool.name}: {tool.description} Parameters: {tool.parameters}")
        return "\n".join(lines)

    def run(self, name: str, args: dict[str, object]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(f"Unknown tool: {name}", is_error=True)
        try:
            result = tool.execute(args, self.context)
        except Exception as exc:  # noqa: BLE001 - tool failures should return observations.
            result = ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)
        return ToolResult(
            output=truncate_output(result.output, self.context.output_limit),
            is_error=result.is_error,
            metadata=result.metadata,
        )


def build_default_registry(workspace_root: str | Path = ".", output_limit: int = 8000) -> ToolRegistry:
    context = ToolContext(
        workspace_root=Path(workspace_root).resolve(),
        file_state=FileStateCache(),
        output_limit=output_limit,
    )
    registry = ToolRegistry(context)
    for tool in [ReadFile(), WriteFile(), EditFile(), FindFile(), Glob(), Grep(), Bash(), GitStatus(), GitDiff()]:
        registry.register(tool)
    return registry
