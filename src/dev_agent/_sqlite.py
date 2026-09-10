"""Shared SQLite connection defaults for short, multi-process transactions."""

from __future__ import annotations

import sqlite3
from pathlib import Path


BUSY_TIMEOUT_MS = 5_000


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a durable connection with bounded lock waiting and WAL enabled."""

    connection = sqlite3.connect(path, check_same_thread=False, timeout=BUSY_TIMEOUT_MS / 1000)
    connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    if str(path) != ":memory:":
        connection.execute("PRAGMA journal_mode=WAL")
    return connection


__all__ = ["BUSY_TIMEOUT_MS", "connect"]
