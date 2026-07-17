from __future__ import annotations

from codeagent.tools.base import ToolContext, ToolResult, display_path, require_str, resolve_workspace_path


class WriteFile:
    name = "write_file"
    description = "Write content to a file, creating parent directories. Existing files must be read first."
    category = "write"
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    def execute(self, args: dict[str, object], context: ToolContext) -> ToolResult:
        path = resolve_workspace_path(context.workspace_root, args.get("path"))
        content = require_str(args, "content", "")
        ok, error = context.file_state.check_writable(path)
        if not ok:
            return ToolResult(error, is_error=True)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            context.file_state.update_after_write(path)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"Error writing file: {exc}", is_error=True)
        return ToolResult(f"Successfully wrote to {display_path(context.workspace_root, path)}")
