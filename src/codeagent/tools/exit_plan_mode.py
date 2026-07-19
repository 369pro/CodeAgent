from __future__ import annotations

from pathlib import Path

from codeagent.tools.base import ToolContext, ToolResult, display_path


class ExitPlanMode:
    name = "exit_plan_mode"
    description = "Signal that the current plan file is ready for user approval."
    category = "read"
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self, plan_path: str | Path | None = None, *, enabled: bool = True) -> None:
        self.plan_path = Path(plan_path).resolve(strict=False) if plan_path else None
        self.enabled = enabled

    def execute(self, args: dict[str, object], context: ToolContext) -> ToolResult:
        if not self.enabled or self.plan_path is None:
            return ToolResult("exit_plan_mode is only available in plan mode.", is_error=True)
        if not self.plan_path.exists():
            return ToolResult(
                f"Plan file does not exist: {display_path(context.workspace_root, self.plan_path)}",
                is_error=True,
            )
        try:
            content = self.plan_path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"Error reading plan file: {exc}", is_error=True)
        if not content.strip():
            return ToolResult("Plan file is empty.", is_error=True)
        return ToolResult(
            "Plan mode will exit after this turn. The user will be asked to approve the plan.",
            metadata={"plan_path": str(self.plan_path)},
        )
