from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    output: str


ToolHandler = Callable[[dict[str, object]], ToolResult]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: ToolHandler


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def names(self) -> list[str]:
        return sorted(self._tools)

    def descriptions(self) -> str:
        return "\n".join(f"- {tool.name}: {tool.description}" for tool in self._tools.values())

    def run(self, name: str, args: dict[str, object]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, f"Unknown tool: {name}")
        try:
            return tool.handler(args)
        except Exception as exc:  # noqa: BLE001 - tool failures should return observations.
            return ToolResult(False, f"{type(exc).__name__}: {exc}")


def _workspace_path(root: Path, raw_path: object) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("'path' must be a non-empty string.")
    candidate = (root / raw_path).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError(f"Path escapes workspace: {raw_path}")
    return candidate


def build_default_registry(workspace_root: str | Path = ".") -> ToolRegistry:
    root = Path(workspace_root).resolve()

    def list_files(args: dict[str, object]) -> ToolResult:
        path = _workspace_path(root, args.get("path", "."))
        if not path.exists():
            return ToolResult(False, f"Path does not exist: {path.relative_to(root)}")
        if path.is_file():
            return ToolResult(True, str(path.relative_to(root)))
        files = sorted(
            str(item.relative_to(root))
            for item in path.rglob("*")
            if item.is_file() and ".git" not in item.parts
        )
        return ToolResult(True, "\n".join(files))

    def read_file(args: dict[str, object]) -> ToolResult:
        path = _workspace_path(root, args.get("path"))
        if not path.is_file():
            return ToolResult(False, f"Not a file: {path.relative_to(root)}")
        return ToolResult(True, path.read_text(encoding="utf-8"))

    def grep(args: dict[str, object]) -> ToolResult:
        pattern = args.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError("'pattern' must be a non-empty string.")
        path = _workspace_path(root, args.get("path", "."))
        regex = re.compile(pattern)
        targets = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
        matches: list[str] = []
        for target in targets:
            if ".git" in target.parts:
                continue
            for line_no, line in enumerate(target.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if regex.search(line):
                    matches.append(f"{target.relative_to(root)}:{line_no}:{line}")
        return ToolResult(True, "\n".join(matches) if matches else "No matches.")

    return ToolRegistry(
        [
            Tool("list_files", "List files under a workspace path. Args: {'path': '.'}", list_files),
            Tool("read_file", "Read a UTF-8 file. Args: {'path': 'README.md'}", read_file),
            Tool("grep", "Search files with a Python regex. Args: {'pattern': 'foo', 'path': '.'}", grep),
        ]
    )
