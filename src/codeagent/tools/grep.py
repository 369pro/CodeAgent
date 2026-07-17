from __future__ import annotations

import re

from codeagent.tools.base import ToolContext, ToolResult, display_path, require_str, resolve_workspace_path, should_skip


class Grep:
    name = "grep"
    description = "Search file contents using a regex pattern, returning file:line:content matches."
    category = "read"
    parameters = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "default": "."},
            "include": {"type": "string", "default": ""},
        },
        "required": ["pattern"],
    }

    def execute(self, args: dict[str, object], context: ToolContext) -> ToolResult:
        base = resolve_workspace_path(context.workspace_root, args.get("path", "."))
        pattern = require_str(args, "pattern")
        include = require_str(args, "include", "")
        if not base.exists():
            return ToolResult(f"Error: path not found: {display_path(context.workspace_root, base)}", is_error=True)
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            return ToolResult(f"Error: invalid regex: {exc}", is_error=True)
        if base.is_file():
            targets = [base]
        else:
            glob_pattern = include or "**/*"
            if include and not glob_pattern.startswith("**/"):
                glob_pattern = "**/" + glob_pattern
            targets = [path for path in base.glob(glob_pattern) if path.is_file()]
        matches: list[str] = []
        for path in sorted(targets):
            if should_skip(path):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for line_no, line in enumerate(lines, 1):
                if regex.search(line):
                    matches.append(f"{display_path(context.workspace_root, path)}:{line_no}:{line}")
        return ToolResult("\n".join(matches) if matches else "No matches found.")
