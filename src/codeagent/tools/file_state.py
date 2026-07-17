from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileSnapshot:
    mtime_ns: int
    size: int


class FileStateCache:
    def __init__(self) -> None:
        self._snapshots: dict[Path, FileSnapshot] = {}

    def record_read(self, path: Path) -> None:
        resolved = path.resolve()
        stat = resolved.stat()
        self._snapshots[resolved] = FileSnapshot(mtime_ns=stat.st_mtime_ns, size=stat.st_size)

    def check_writable(self, path: Path) -> tuple[bool, str]:
        resolved = path.resolve()
        if not resolved.exists():
            return True, ""
        snapshot = self._snapshots.get(resolved)
        if snapshot is None:
            return False, f"Error: read_file must be called before modifying existing file: {path}"
        stat = resolved.stat()
        if snapshot.mtime_ns != stat.st_mtime_ns or snapshot.size != stat.st_size:
            return False, f"Error: file changed since last read_file, read it again before writing: {path}"
        return True, ""

    def update_after_write(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved.exists():
            self.record_read(resolved)
