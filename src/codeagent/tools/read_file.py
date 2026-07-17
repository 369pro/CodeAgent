from __future__ import annotations

from codeagent.tools.base import ToolContext, ToolResult, display_path, require_int, resolve_workspace_path


class ReadFile:
    name = "read_file"
    description = "Read a UTF-8 file and return contents with line numbers."
    category = "read"
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "default": 0},
            "limit": {"type": "integer", "default": 2000},
        },
        "required": ["path"],
    }

    def execute(self, args: dict[str, object], context: ToolContext) -> ToolResult:
        path = resolve_workspace_path(context.workspace_root, args.get("path"))
        offset = max(0, require_int(args, "offset", 0))
        limit = max(1, require_int(args, "limit", 2000))
        if not path.is_file():
            return ToolResult(f"Error: not a file: {display_path(context.workspace_root, path)}", is_error=True)
        try:
            text = path.read_text(encoding="utf-8")
            context.file_state.record_read(path)
        except Exception as exc:  # noqa: BLE001 - tool failures become observations.
            return ToolResult(f"Error reading file: {exc}", is_error=True)
        lines = text.splitlines()
        selected = lines[offset : offset + limit]
        numbered = [f"{index + offset + 1}\t{line}" for index, line in enumerate(selected)]
        return ToolResult("\n".join(numbered))
