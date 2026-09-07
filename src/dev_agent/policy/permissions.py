"""Canonical path capability checks with symlink escape protection."""

from __future__ import annotations

from pathlib import Path


class PathPolicy:
    def __init__(self, workspace_root: str | Path, rules: dict[str, set[str]] | None = None) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.rules = {str(Path(key).as_posix()).rstrip("/"): set(value) for key, value in (rules or {}).items()}

    def canonical(self, path: str | Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.workspace_root / candidate
        return candidate.resolve(strict=False)

    def check(self, path: str | Path, operation: str) -> bool:
        try:
            target = self.canonical(path)
            target.relative_to(self.workspace_root)
        except ValueError:
            return False
        for relative_root, operations in self.rules.items():
            allowed_root = (self.workspace_root / relative_root).resolve(strict=False)
            try:
                target.relative_to(allowed_root)
            except ValueError:
                continue
            return operation in operations
        return False

    def require(self, path: str | Path, operation: str) -> Path:
        canonical = self.canonical(path)
        if not self.check(canonical, operation):
            raise PermissionError(f"policy denied {operation}: {canonical}")
        return canonical
