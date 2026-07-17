from __future__ import annotations

from codeagent.tools.base import ToolContext, ToolResult, display_path, require_str, resolve_workspace_path


class EditFile:
    name = "edit_file"
    description = "Replace an exact unique string in an existing file. The file must be read first."
    category = "write"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        "required": ["path", "old_string", "new_string"],
    }

    def execute(self, args: dict[str, object], context: ToolContext) -> ToolResult:
        path = resolve_workspace_path(context.workspace_root, args.get("path"))
        old_string = require_str(args, "old_string")
        new_string = require_str(args, "new_string", "")
        if not path.is_file():
            return ToolResult(f"Error: file not found: {display_path(context.workspace_root, path)}", is_error=True)
        ok, error = context.file_state.check_writable(path)
        if not ok:
            return ToolResult(error, is_error=True)
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"Error reading file: {exc}", is_error=True)
        count = content.count(old_string)
        if count == 0:
            return ToolResult("Error: old_string not found in file", is_error=True)
        if count > 1:
            return ToolResult(f"Error: old_string found {count} times, must be unique", is_error=True)
        try:
            path.write_text(content.replace(old_string, new_string, 1), encoding="utf-8")
            context.file_state.update_after_write(path)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"Error writing file: {exc}", is_error=True)
        return ToolResult(f"Successfully edited {display_path(context.workspace_root, path)}")
