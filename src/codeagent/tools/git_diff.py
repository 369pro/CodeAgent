from __future__ import annotations

import subprocess

from codeagent.tools.base import ToolContext, ToolResult, require_bool, require_str, resolve_workspace_path


class GitDiff:
    name = "git_diff"
    description = "Show git diff. Args: {'cached': false, 'path': ''}"
    category = "read"
    parameters = {
        "type": "object",
        "properties": {
            "cached": {"type": "boolean", "default": False},
            "path": {"type": "string", "default": ""},
        },
    }

    def execute(self, args: dict[str, object], context: ToolContext) -> ToolResult:
        cached = require_bool(args, "cached", False)
        raw_path = require_str(args, "path", "")
        command = ["git", "diff"]
        if cached:
            command.append("--cached")
        if raw_path:
            path = resolve_workspace_path(context.workspace_root, raw_path)
            command.extend(["--", str(path.relative_to(context.workspace_root))])
        completed = subprocess.run(command, cwd=context.workspace_root, text=True, capture_output=True, check=False)
        output = completed.stdout or completed.stderr or "(no output)"
        return ToolResult(output, is_error=completed.returncode != 0, metadata={"returncode": completed.returncode})
