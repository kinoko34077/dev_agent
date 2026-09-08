"""Offline SQLite backup/restore helpers for recovery operations."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import shutil
import sqlite3
import tempfile
from typing import Iterator


def _readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    return sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)


def _integrity_ok(connection: sqlite3.Connection) -> bool:
    row = connection.execute("PRAGMA integrity_check").fetchone()
    return bool(row and row[0] == "ok")


def _logical_dump(connection: sqlite3.Connection) -> str:
    return "\n".join(connection.iterdump())


def backup_sqlite(source: str | Path, destination: str | Path) -> Path:
    """Create a consistent SQLite backup and atomically publish its path."""
    src_path, dst_path = Path(source), Path(destination)
    if src_path.resolve() == dst_path.resolve():
        raise ValueError("backup destination must differ from source")
    if not src_path.is_file():
        raise FileNotFoundError(src_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{dst_path.name}.", suffix=".tmp", dir=dst_path.parent, delete=False) as temporary:
            temporary_name = temporary.name
        src = sqlite3.connect(src_path)
        dst = sqlite3.connect(temporary_name)
        try:
            src.backup(dst)
            if not _integrity_ok(dst):
                raise sqlite3.DatabaseError("backup integrity check failed")
        finally:
            dst.close()
            src.close()
        os.replace(temporary_name, dst_path)
        temporary_name = None
        return dst_path
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def validate_backup(source: str | Path, backup: str | Path) -> bool:
    """Validate integrity, schema, and logical contents of a SQLite backup."""
    source_path, backup_path = Path(source), Path(backup)
    if source_path.resolve() == backup_path.resolve():
        return False
    src: sqlite3.Connection | None = None
    copy: sqlite3.Connection | None = None
    try:
        src = _readonly(source_path)
        copy = _readonly(backup_path)
        if not _integrity_ok(src) or not _integrity_ok(copy):
            return False
        source_dump = _logical_dump(src)
        backup_dump = _logical_dump(copy)
        return source_dump == backup_dump
    except (OSError, sqlite3.Error):
        return False
    finally:
        if copy is not None:
            copy.close()
        if src is not None:
            src.close()


def restore_sqlite(source: str | Path, destination: str | Path, *, replace: bool = False) -> Path:
    """Restore a backup to a new path, publishing it only after validation.

    Existing destinations require ``replace=True`` so an operator cannot
    silently overwrite a live state database.
    """
    source_path, dst_path = Path(source), Path(destination)
    if source_path.resolve() == dst_path.resolve():
        raise ValueError("restore destination must differ from source")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if dst_path.exists() and not replace:
        raise FileExistsError(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{dst_path.name}.", suffix=".restore.tmp", dir=dst_path.parent, delete=False) as temporary:
            temporary_name = temporary.name
        src = sqlite3.connect(source_path)
        restored = sqlite3.connect(temporary_name)
        try:
            src.backup(restored)
            if not _integrity_ok(restored):
                raise sqlite3.DatabaseError("restored database integrity check failed")
        finally:
            restored.close()
            src.close()
        if not validate_backup(source_path, temporary_name):
            raise sqlite3.DatabaseError("restored database content validation failed")
        os.replace(temporary_name, dst_path)
        temporary_name = None
        return dst_path
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def _copy_artifact_root(source: str | Path, destination: str | Path) -> Path:
    from .validate_artifacts import validate_artifact_root

    source_path, destination_path = Path(source).expanduser().resolve(), Path(destination).expanduser().resolve()
    if source_path == destination_path:
        raise ValueError("artifact destination must differ from source")
    if not source_path.is_dir():
        raise FileNotFoundError(source_path)
    if destination_path.exists():
        raise FileExistsError(destination_path)
    source_ok, source_detail = validate_artifact_root(source_path)
    if not source_ok:
        raise ValueError(f"artifact source failed validation: {source_detail}")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = Path(tempfile.mkdtemp(prefix=f".{destination_path.name}.", dir=destination_path.parent))
    try:
        for item in source_path.iterdir():
            if item.is_file() and item.suffix in {".bin", ".json"}:
                shutil.copy2(item, temporary_path / item.name)
        ok, detail = validate_artifact_root(temporary_path)
        if not ok:
            raise ValueError(f"artifact copy failed validation: {detail}")
        os.replace(temporary_path, destination_path)
        return destination_path
    finally:
        if temporary_path.exists():
            shutil.rmtree(temporary_path)


def backup_artifact_root(source: str | Path, destination: str | Path) -> Path:
    """Atomically copy a validated event-artifact root without sidecar drift."""
    return _copy_artifact_root(source, destination)


def restore_artifact_root(source: str | Path, destination: str | Path) -> Path:
    """Restore a validated artifact-root copy to a new, non-existing path."""
    return _copy_artifact_root(source, destination)


@contextmanager
def maintenance_lock(lock_path: str | Path) -> Iterator[None]:
    """Exclusive create lock preventing concurrent recovery/normal maintenance."""
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    try:
        with path.open("x", encoding="utf-8") as stream:
            acquired = True
            stream.write("maintenance\n")
        yield
    finally:
        # A failed exclusive create must not remove the lock owned by another
        # process (the old implementation did exactly that on nested use).
        if acquired:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


__all__ = ["backup_artifact_root", "backup_sqlite", "maintenance_lock", "restore_artifact_root", "restore_sqlite", "validate_backup"]
