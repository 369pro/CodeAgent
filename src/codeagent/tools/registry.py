from __future__ import annotations

from pathlib import Path
from typing import Literal

from codeagent.permissions import ApprovalScope, PermissionChecker
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
    def __init__(
        self, context: ToolContext, permission_checker: PermissionChecker | None = None
    ) -> None:
        self.context = context
        self.permission_checker = permission_checker
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def read_only(self) -> "ToolRegistry":
        registry = ToolRegistry(self.context, self.permission_checker)
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

    def run(
        self,
        name: str,
        args: dict[str, object],
        *,
        approval_scope: ApprovalScope | Literal["deny"] | None = None,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(f"Unknown tool: {name}", is_error=True)
        if self.permission_checker is not None:
            decision = self.permission_checker.check(tool, args)
            if decision.effect == "deny":
                return ToolResult(
                    f"Permission denied: {decision.reason}",
                    is_error=True,
                    metadata={"permission": decision.reason},
                )
            if decision.effect == "ask":
                if approval_scope in {"once", "session", "permanent"}:
                    self.permission_checker.approve(
                        tool.name, decision.normalized_content or decision.content, approval_scope
                    )
                else:
                    return ToolResult(
                        f"Permission denied: {decision.reason}",
                        is_error=True,
                        metadata={"permission": decision.reason},
                    )
        try:
            result = tool.execute(args, self.context)
        except Exception as exc:  # noqa: BLE001 - tool failures should return observations.
            result = ToolResult(f"{type(exc).__name__}: {exc}", is_error=True)
        return ToolResult(
            output=truncate_output(result.output, self.context.output_limit),
            is_error=result.is_error,
            metadata=result.metadata,
        )


def build_default_registry(
    workspace_root: str | Path = ".",
    output_limit: int = 8000,
    permission_checker: PermissionChecker | None = None,
) -> ToolRegistry:
    context = ToolContext(
        workspace_root=Path(workspace_root).resolve(),
        file_state=FileStateCache(),
        output_limit=output_limit,
    )
    registry = ToolRegistry(context, permission_checker)
    for tool in [ReadFile(), WriteFile(), EditFile(), FindFile(), Glob(), Grep(), Bash(), GitStatus(), GitDiff()]:
        registry.register(tool)
    return registry
