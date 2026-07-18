from __future__ import annotations

import subprocess

from codeagent.tools.base import ToolContext, ToolResult, require_int, require_str


MAX_TIMEOUT = 600


class Bash:
    name = "bash"
    description = "Execute a shell command in the workspace and return stdout and stderr. Prefer dedicated tools when available."
    category = "command"
    parameters = {
        "type": "object",
        "properties": {"command": {"type": "string"}, "timeout": {"type": "integer", "default": 120}},
        "required": ["command"],
    }

    def execute(self, args: dict[str, object], context: ToolContext) -> ToolResult:
        command = require_str(args, "command")
        timeout = min(max(1, require_int(args, "timeout", 120)), MAX_TIMEOUT)
        try:
            completed = subprocess.run(
                command,
                cwd=context.workspace_root,
                shell=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ToolResult(f"Error: command timed out after {timeout}s", is_error=True, metadata={"timeout": timeout})
        except Exception as exc:  # noqa: BLE001
            return ToolResult(f"Error executing command: {exc}", is_error=True)

        parts: list[str] = []
        if completed.stdout:
            parts.append(f"STDOUT:\n{completed.stdout}")
        if completed.stderr:
            parts.append(f"STDERR:\n{completed.stderr}")
        output = "\n".join(parts) if parts else "(no output)"
        return ToolResult(output, is_error=completed.returncode != 0, metadata={"returncode": completed.returncode})
