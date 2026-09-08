"""Phase 6 recovery operator facade with explicit mutation gates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .backup import backup_sqlite, maintenance_lock, restore_sqlite, validate_backup
from .git_recovery import create_repair_branch, inspect_git, plan_rollback, record_last_known_good
from .validate_sqlite_state import validate_sqlite_state


class RecoveryOperator:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def snapshot(self) -> dict[str, Any]:
        result: dict[str, Any] = {"repository_root": self.root.is_dir(), "root": str(self.root)}
        if (self.root / ".git").exists():
            result["git"] = inspect_git(self.root).__dict__
        return result

    def backup_state(self, source: str | Path, destination: str | Path) -> Path:
        return backup_sqlite(source, destination)

    def restore_state(self, source: str | Path, destination: str | Path, *, allow_write: bool = False, lock_path: str | Path | None = None, replace: bool = False) -> Path:
        if not allow_write:
            raise PermissionError("restore requires allow_write=True")
        lock = Path(lock_path) if lock_path is not None else Path(destination).with_suffix(Path(destination).suffix + ".maintenance.lock")
        with maintenance_lock(lock):
            restored = restore_sqlite(source, destination, replace=replace)
            ok, detail = validate_sqlite_state(restored)
            if not ok:
                raise ValueError(f"restored state failed validation: {detail}")
            return restored

    def validate_state(self, database: str | Path) -> tuple[bool, str]:
        return validate_sqlite_state(database)

    def record_lkg(self, metadata_path: str | Path, *, commit: str | None = None, require_clean: bool = False, test_report: str | None = None) -> Path:
        return record_last_known_good(self.root, metadata_path, commit=commit, require_clean=require_clean, test_report=test_report)

    def rollback_plan(self, metadata_path: str | Path):
        return plan_rollback(self.root, metadata_path)

    def create_repair_branch(self, metadata_path: str | Path, branch_name: str, *, allow_write: bool = False) -> str:
        return create_repair_branch(self.root, metadata_path, branch_name, allow_write=allow_write)


__all__ = ["RecoveryOperator"]

