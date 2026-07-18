from __future__ import annotations

from enum import Enum
from typing import Literal

from codeagent.tools.base import ToolCategory


DecisionEffect = Literal["allow", "deny", "ask"]


class PermissionMode(str, Enum):
    STRICT = "strict"
    DEFAULT = "default"
    ACCEPT_EDITS = "acceptEdits"
    PLAN = "plan"
    BYPASS = "bypassPermissions"
    DONT_ASK = "dontAsk"


_MODE_MATRIX: dict[PermissionMode, dict[ToolCategory, DecisionEffect]] = {
    PermissionMode.STRICT: {"read": "ask", "write": "ask", "command": "ask"},
    PermissionMode.DEFAULT: {"read": "allow", "write": "ask", "command": "ask"},
    PermissionMode.ACCEPT_EDITS: {
        "read": "allow",
        "write": "allow",
        "command": "ask",
    },
    PermissionMode.PLAN: {"read": "allow", "write": "deny", "command": "deny"},
    PermissionMode.BYPASS: {"read": "allow", "write": "allow", "command": "allow"},
    PermissionMode.DONT_ASK: {"read": "allow", "write": "allow", "command": "allow"},
}


def mode_decide(mode: PermissionMode, category: ToolCategory) -> DecisionEffect:
    return _MODE_MATRIX[mode][category]
