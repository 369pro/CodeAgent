from __future__ import annotations

from pathlib import Path


class PathSandbox:
    def __init__(self, project_root: str | Path, extra_allowed: list[str | Path] | None = None) -> None:
        self._allowed_roots = [Path(project_root).expanduser().resolve()]
        for path in extra_allowed or []:
            self._allowed_roots.append(Path(path).expanduser().resolve())

    @property
    def project_root(self) -> Path:
        return self._allowed_roots[0]

    def check(self, raw_path: str) -> tuple[bool, str]:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = self.project_root / path
        absolute = path.absolute()
        try:
            real_path = absolute.resolve(strict=True)
        except OSError:
            ancestor = absolute
            while not ancestor.exists():
                parent = ancestor.parent
                if parent == ancestor:
                    return False, f"cannot resolve path: {raw_path}"
                ancestor = parent
            try:
                resolved_ancestor = ancestor.resolve(strict=True)
            except OSError:
                return False, f"cannot resolve path: {raw_path}"
            real_path = resolved_ancestor / absolute.relative_to(ancestor)

        for root in self._allowed_roots:
            try:
                real_path.relative_to(root)
                return True, ""
            except ValueError:
                continue
        return False, f"path escapes sandbox: {raw_path}"
