"""Canonical path capability checks with symlink escape protection."""

from __future__ import annotations

from pathlib import Path
from pathlib import PurePosixPath


class PathPolicy:
    def __init__(self, workspace_root: str | Path, rules: dict[str, set[str]] | None = None) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        normalized_rules: dict[str, set[str]] = {}
        for key, value in (rules or {}).items():
            relative = str(key).replace("\\", "/")
            relative = str(PurePosixPath(relative)).rstrip("/")
            if relative == ".":
                relative = ""
            normalized_rules[relative] = set(value)
        self.rules = normalized_rules

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
        matches: list[tuple[int, set[str]]] = []
        for relative_root, operations in self.rules.items():
            allowed_root = (self.workspace_root / relative_root).resolve(strict=False)
            try:
                target.relative_to(allowed_root)
            except ValueError:
                continue
            # A nested rule is more specific than its parent.  Selecting the
            # longest matching root makes deny/allow behavior independent of
            # dictionary insertion order.
            depth = len(PurePosixPath(relative_root).parts) if relative_root else 0
            matches.append((depth, operations))
        if matches:
            _depth, operations = max(matches, key=lambda item: item[0])
            return operation in operations
        return False

    def require(self, path: str | Path, operation: str) -> Path:
        canonical = self.canonical(path)
        if not self.check(canonical, operation):
            raise PermissionError(f"policy denied {operation}: {canonical}")
        return canonical
