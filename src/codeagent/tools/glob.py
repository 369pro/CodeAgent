from __future__ import annotations

from codeagent.tools.base import ToolContext, ToolResult, display_path, require_str, resolve_workspace_path, should_skip


class Glob:
    name = "glob"
    description = "Find files matching a glob pattern under a workspace path."
    category = "read"
    parameters = {
        "type": "object",
        "properties": {"pattern": {"type": "string"}, "path": {"type": "string", "default": "."}},
        "required": ["pattern"],
    }

    def execute(self, args: dict[str, object], context: ToolContext) -> ToolResult:
        base = resolve_workspace_path(context.workspace_root, args.get("path", "."))
        pattern = require_str(args, "pattern")
        if not base.exists():
            return ToolResult(f"Error: path not found: {display_path(context.workspace_root, base)}", is_error=True)
        try:
            matches = sorted(
                display_path(context.workspace_root, path)
                for path in base.glob(pattern)
                if path.is_file() and not should_skip(path)
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"Error: {exc}", is_error=True)
        return ToolResult("\n".join(matches) if matches else "No files matched the pattern.")
