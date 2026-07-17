from __future__ import annotations

import subprocess

from codeagent.tools.base import ToolContext, ToolResult, require_bool


class GitStatus:
    name = "git_status"
    description = "Show git working tree status. Args: {'short': true}"
    category = "read"
    parameters = {
        "type": "object",
        "properties": {"short": {"type": "boolean", "default": True}},
    }

    def execute(self, args: dict[str, object], context: ToolContext) -> ToolResult:
        short = require_bool(args, "short", True)
        command = ["git", "status", "--short"] if short else ["git", "status"]
        completed = subprocess.run(command, cwd=context.workspace_root, text=True, capture_output=True, check=False)
        output = completed.stdout or completed.stderr or "(no output)"
        return ToolResult(output, is_error=completed.returncode != 0, metadata={"returncode": completed.returncode})
