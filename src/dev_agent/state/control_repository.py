"""Durable operation control records shared by the Operation facade."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from .._sqlite import connect


class OperationControl:
    """Durable stop signal shared by separate ``start`` and ``stop`` calls."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS operation_control (
        id INTEGER PRIMARY KEY CHECK (id=1),
        stop_requested INTEGER NOT NULL DEFAULT 0
    );
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = connect(self.path)
        self._lock = RLock()
        self.connection.executescript(self._SCHEMA)
        self.connection.execute("INSERT OR IGNORE INTO operation_control(id, stop_requested) VALUES (1, 0)")
        self.connection.commit()

    def request_stop(self) -> None:
        with self._lock:
            self.connection.execute("UPDATE operation_control SET stop_requested=1 WHERE id=1")
            self.connection.commit()

    def clear_stop(self) -> None:
        with self._lock:
            self.connection.execute("UPDATE operation_control SET stop_requested=0 WHERE id=1")
            self.connection.commit()

    def stop_requested(self) -> bool:
        with self._lock:
            row = self.connection.execute("SELECT stop_requested FROM operation_control WHERE id=1").fetchone()
            return bool(row and row[0])

    def close(self) -> None:
        with self._lock:
            self.connection.close()
