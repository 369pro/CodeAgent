from __future__ import annotations

from codeagent.tools.base import ToolContext, ToolResult, display_path, require_int, require_str, resolve_workspace_path, should_skip


class FindFile:
    name = "find_file"
    description = "Recursively find files by exact filename or substring under a workspace path."
    category = "read"
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "path": {"type": "string", "default": "."},
            "contains": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "default": 50},
        },
        "required": ["name"],
    }

    def execute(self, args: dict[str, object], context: ToolContext) -> ToolResult:
        base = resolve_workspace_path(context.workspace_root, args.get("path", "."))
        name = require_str(args, "name")
        contains = bool(args.get("contains", False))
        limit = max(1, require_int(args, "limit", 50))
        if not base.exists():
            return ToolResult(f"Error: path not found: {display_path(context.workspace_root, base)}", is_error=True)

        matches: list[str] = []
        for path in sorted(base.rglob("*")):
            if len(matches) >= limit:
                break
            if not path.is_file() or should_skip(path):
                continue
            matched = name in path.name if contains else path.name == name
            if matched:
                matches.append(display_path(context.workspace_root, path))

        if not matches:
            mode = "containing" if contains else "named"
            return ToolResult(f"No files {mode} {name!r} were found under {display_path(context.workspace_root, base)}.")
        suffix = "\n...[truncated]" if len(matches) >= limit else ""
        return ToolResult("\n".join(matches) + suffix)
