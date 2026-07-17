from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from codeagent.tools.file_state import FileStateCache


SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".tox", ".mypy_cache"}
ToolCategory = Literal["read", "write", "command"]


@dataclass(frozen=True)
class ToolResult:
    output: str
    is_error: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass
class ToolContext:
    workspace_root: Path
    file_state: FileStateCache
    output_limit: int = 8000


class Tool(Protocol):
    name: str
    description: str
    category: ToolCategory
    parameters: dict[str, object]

    def execute(self, args: dict[str, object], context: ToolContext) -> ToolResult:
        ...


def require_str(args: dict[str, object], name: str, default: str | None = None) -> str:
    value = args.get(name, default)
    if not isinstance(value, str) or (default is None and not value):
        raise ValueError(f"'{name}' must be a non-empty string.")
    return value


def require_int(args: dict[str, object], name: str, default: int) -> int:
    value = args.get(name, default)
    if not isinstance(value, int):
        raise ValueError(f"'{name}' must be an integer.")
    return value


def require_bool(args: dict[str, object], name: str, default: bool) -> bool:
    value = args.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"'{name}' must be a boolean.")
    return value


def resolve_workspace_path(root: Path, raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("'path' must be a non-empty string.")
    candidate = (root / raw_path).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError(f"Path escapes workspace: {raw_path}")
    return candidate


def display_path(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def truncate_output(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)
