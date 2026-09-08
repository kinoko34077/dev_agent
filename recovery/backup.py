"""Offline SQLite backup/restore helpers for recovery operations."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator


def backup_sqlite(source: str | Path, destination: str | Path) -> Path:
    src_path, dst_path = Path(source), Path(destination)
    if src_path.resolve() == dst_path.resolve():
        raise ValueError("backup destination must differ from source")
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(src_path) as src, sqlite3.connect(dst_path) as dst:
        src.backup(dst)
    return dst_path


def validate_backup(source: str | Path, backup: str | Path) -> bool:
    with sqlite3.connect(source) as src, sqlite3.connect(backup) as copy:
        source_tables = {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        copy_tables = {row[0] for row in copy.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return source_tables == copy_tables


@contextmanager
def maintenance_lock(lock_path: str | Path) -> Iterator[None]:
    """Exclusive create lock preventing concurrent recovery/normal maintenance."""
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write("maintenance\n")
        yield
    finally:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
