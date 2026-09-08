"""SQLite durable queue with lease and state-version fencing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import RLock
import time


class QueueEmpty(RuntimeError):
    pass


class StaleLease(RuntimeError):
    pass


def _epoch(value: datetime | float | int | None) -> float:
    if value is None:
        return time.time()
    if isinstance(value, datetime):
        return value.timestamp()
    return float(value)


@dataclass(frozen=True)
class QueueItem:
    task_id: str
    priority: int
    run_at: datetime
    state: str
    lease_owner: str | None
    lease_until: datetime | None
    state_version: int
    attempts: int


class DurableQueue:
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS queue_items (
        task_id TEXT PRIMARY KEY,
        priority INTEGER NOT NULL,
        run_at REAL NOT NULL,
        state TEXT NOT NULL,
        lease_owner TEXT,
        lease_until REAL,
        state_version INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0
    );
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(self._SCHEMA)
        self.connection.commit()
        self._lock = RLock()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "DurableQueue":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _item(row: sqlite3.Row) -> QueueItem:
        return QueueItem(row["task_id"], row["priority"], datetime.fromtimestamp(row["run_at"], timezone.utc), row["state"], row["lease_owner"], datetime.fromtimestamp(row["lease_until"], timezone.utc) if row["lease_until"] is not None else None, row["state_version"], row["attempts"])

    def enqueue(self, task_id: str, *, run_at: datetime | float | int | None = None, priority: int = 0) -> QueueItem:
        if not task_id.strip() or isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError("task_id and integer priority are required")
        with self._lock:
            self.connection.execute("INSERT INTO queue_items VALUES (?, ?, ?, 'queued', NULL, NULL, 1, 0)", (task_id, priority, _epoch(run_at)))
            self.connection.commit()
            return self.snapshot(task_id)

    def claim(self, worker_id: str, *, now: datetime | float | int | None = None, lease_seconds: float = 30.0) -> QueueItem:
        if not worker_id.strip() or lease_seconds <= 0:
            raise ValueError("worker_id and positive lease_seconds are required")
        current = _epoch(now)
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.connection.execute("SELECT * FROM queue_items WHERE (state='queued' AND run_at <= ?) OR (state='leased' AND lease_until <= ?) ORDER BY priority DESC, run_at ASC, task_id ASC LIMIT 1", (current, current)).fetchone()
                if row is None:
                    self.connection.rollback()
                    raise QueueEmpty("no queue item is ready")
                version = int(row["state_version"]) + 1
                until = current + lease_seconds
                cursor = self.connection.execute("UPDATE queue_items SET state='leased', lease_owner=?, lease_until=?, state_version=?, attempts=attempts+1 WHERE task_id=? AND state_version=? AND (state='queued' OR (state='leased' AND lease_until <= ?))", (worker_id, until, version, row["task_id"], row["state_version"], current))
                if cursor.rowcount != 1:
                    self.connection.rollback()
                    raise QueueEmpty("queue claim lost race")
                self.connection.commit()
            except Exception:
                if self.connection.in_transaction:
                    self.connection.rollback()
                raise
            return self.snapshot(row["task_id"])

    def renew(self, task_id: str, *, worker_id: str, state_version: int, lease_seconds: float = 30.0) -> QueueItem:
        with self._lock:
            cursor = self.connection.execute("UPDATE queue_items SET lease_until=? WHERE task_id=? AND state='leased' AND lease_owner=? AND state_version=?", (time.time() + lease_seconds, task_id, worker_id, state_version))
            self.connection.commit()
            if cursor.rowcount != 1:
                raise StaleLease(task_id)
            return self.snapshot(task_id)

    def complete(self, task_id: str, *, worker_id: str, state_version: int) -> QueueItem:
        return self._finish(task_id, worker_id=worker_id, state_version=state_version, state="completed")

    def fail(self, task_id: str, *, worker_id: str, state_version: int, retry: bool = False) -> QueueItem:
        return self._finish(task_id, worker_id=worker_id, state_version=state_version, state="queued" if retry else "failed")

    def _finish(self, task_id: str, *, worker_id: str, state_version: int, state: str) -> QueueItem:
        with self._lock:
            cursor = self.connection.execute("UPDATE queue_items SET state=?, lease_owner=NULL, lease_until=NULL, state_version=state_version+1 WHERE task_id=? AND state='leased' AND lease_owner=? AND state_version=?", (state, task_id, worker_id, state_version))
            self.connection.commit()
            if cursor.rowcount != 1:
                raise StaleLease(task_id)
            return self.snapshot(task_id)

    def reap_expired(self, *, now: datetime | float | int | None = None) -> int:
        current = _epoch(now)
        with self._lock:
            cursor = self.connection.execute("UPDATE queue_items SET state='queued', lease_owner=NULL, lease_until=NULL, state_version=state_version+1 WHERE state='leased' AND lease_until <= ?", (current,))
            self.connection.commit()
            return cursor.rowcount

    def snapshot(self, task_id: str) -> QueueItem:
        row = self.connection.execute("SELECT * FROM queue_items WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return self._item(row)

