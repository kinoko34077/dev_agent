"""SQLite durable queue with lease and state-version fencing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3
from threading import RLock
import time
from uuid import uuid4

from .._sqlite import connect
from ..persistence.lease import LeaseProof, StaleLease, assert_active_lease


class QueueEmpty(RuntimeError):
    pass


class MaintenanceMode(RuntimeError):
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
    lease_token: str | None = None
    max_attempts: int = 3
    wake_at: datetime | None = None
    wake_reason: str | None = None
    claim_count: int = 0
    execution_attempts: int = 0
    max_execution_attempts: int = 3

    @property
    def lease_proof(self) -> LeaseProof | None:
        if self.lease_owner is None or self.lease_token is None:
            return None
        return LeaseProof(self.task_id, self.lease_owner, self.lease_token, self.state_version)


class DurableQueue:
    DEFAULT_MAX_ATTEMPTS = 3
    SCHEMA_VERSION = 5
    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS queue_items (
        task_id TEXT PRIMARY KEY,
        priority INTEGER NOT NULL,
        run_at REAL NOT NULL,
        state TEXT NOT NULL,
        lease_owner TEXT,
        lease_until REAL,
        lease_token TEXT,
        state_version INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 3,
        wake_at REAL,
        wake_reason TEXT,
        claim_count INTEGER NOT NULL DEFAULT 0,
        execution_attempts INTEGER NOT NULL DEFAULT 0,
        max_execution_attempts INTEGER NOT NULL DEFAULT 3
    );
    CREATE TABLE IF NOT EXISTS scheduler_control (id INTEGER PRIMARY KEY CHECK (id=1), maintenance INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS scheduler_schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        existing_tables = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        try:
            self.connection.executescript(self._SCHEMA)
            if not existing_tables:
                self.connection.execute("INSERT OR REPLACE INTO scheduler_schema_meta(key, value) VALUES ('schema_version', ?)", (str(self.SCHEMA_VERSION),))
                self.connection.commit()
            else:
                self.connection.execute("INSERT OR IGNORE INTO scheduler_schema_meta(key, value) VALUES ('schema_version', '1')")
                current = int(self.connection.execute("SELECT value FROM scheduler_schema_meta WHERE key='schema_version'").fetchone()[0])
                if current > self.SCHEMA_VERSION:
                    raise ValueError(f"unsupported queue schema version: {current}")
                self.connection.commit()
                self.connection.execute("BEGIN")
                if current < 2:
                    columns = {row[1] for row in self.connection.execute("PRAGMA table_info(queue_items)")}
                    if "lease_token" not in columns:
                        self.connection.execute("ALTER TABLE queue_items ADD COLUMN lease_token TEXT")
                    self.connection.execute("UPDATE scheduler_schema_meta SET value='2' WHERE key='schema_version'")
                    current = 2
                if current < 3:
                    columns = {row[1] for row in self.connection.execute("PRAGMA table_info(queue_items)")}
                    if "max_attempts" not in columns:
                        self.connection.execute("ALTER TABLE queue_items ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3")
                    self.connection.execute("UPDATE queue_items SET max_attempts=3 WHERE max_attempts IS NULL OR max_attempts <= 0")
                    self.connection.execute("UPDATE scheduler_schema_meta SET value='3' WHERE key='schema_version'")
                if current < 4:
                    columns = {row[1] for row in self.connection.execute("PRAGMA table_info(queue_items)")}
                    if "wake_at" not in columns:
                        self.connection.execute("ALTER TABLE queue_items ADD COLUMN wake_at REAL")
                    if "wake_reason" not in columns:
                        self.connection.execute("ALTER TABLE queue_items ADD COLUMN wake_reason TEXT")
                    self.connection.execute("UPDATE scheduler_schema_meta SET value='4' WHERE key='schema_version'")
                    current = 4
                if current < 5:
                    columns = {row[1] for row in self.connection.execute("PRAGMA table_info(queue_items)")}
                    if "claim_count" not in columns:
                        self.connection.execute("ALTER TABLE queue_items ADD COLUMN claim_count INTEGER NOT NULL DEFAULT 0")
                    if "execution_attempts" not in columns:
                        self.connection.execute("ALTER TABLE queue_items ADD COLUMN execution_attempts INTEGER NOT NULL DEFAULT 0")
                    if "max_execution_attempts" not in columns:
                        self.connection.execute("ALTER TABLE queue_items ADD COLUMN max_execution_attempts INTEGER NOT NULL DEFAULT 3")
                    self.connection.execute("UPDATE queue_items SET claim_count=attempts WHERE claim_count=0 AND attempts > 0")
                    self.connection.execute("UPDATE queue_items SET max_execution_attempts=max_attempts")
                    self.connection.execute("UPDATE scheduler_schema_meta SET value='5' WHERE key='schema_version'")
                self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "DurableQueue":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @staticmethod
    def _item(row: sqlite3.Row) -> QueueItem:
        return QueueItem(
            row["task_id"],
            row["priority"],
            datetime.fromtimestamp(row["run_at"], timezone.utc),
            row["state"],
            row["lease_owner"],
            datetime.fromtimestamp(row["lease_until"], timezone.utc) if row["lease_until"] is not None else None,
            row["state_version"],
            row["attempts"],
            row["lease_token"],
            row["max_attempts"],
            datetime.fromtimestamp(row["wake_at"], timezone.utc) if row["wake_at"] is not None else None,
            row["wake_reason"],
            row["claim_count"],
            row["execution_attempts"],
            row["max_execution_attempts"],
        )

    @staticmethod
    def _validate_max_attempts(max_attempts: int) -> None:
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts <= 0:
            raise ValueError("max_attempts must be a positive integer")

    def enqueue(self, task_id: str, *, run_at: datetime | float | int | None = None, priority: int = 0, max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> QueueItem:
        if not task_id.strip() or isinstance(priority, bool) or not isinstance(priority, int):
            raise ValueError("task_id and integer priority are required")
        self._validate_max_attempts(max_attempts)
        with self._lock:
            self.connection.execute("INSERT INTO queue_items(task_id, priority, run_at, state, lease_owner, lease_until, lease_token, state_version, attempts, max_attempts, claim_count, execution_attempts, max_execution_attempts) VALUES (?, ?, ?, 'queued', NULL, NULL, NULL, 1, 0, ?, 0, 0, ?)", (task_id, priority, _epoch(run_at), max_attempts, max_attempts))
            self.connection.commit()
            return self.snapshot(task_id)

    def claim(self, worker_id: str, *, now: datetime | float | int | None = None, lease_seconds: float = 30.0) -> QueueItem:
        if not worker_id.strip() or lease_seconds <= 0:
            raise ValueError("worker_id and positive lease_seconds are required")
        current = _epoch(now)
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                control = self.connection.execute("SELECT maintenance FROM scheduler_control WHERE id=1").fetchone()
                if control is not None and control[0]:
                    self.connection.rollback()
                    raise MaintenanceMode("scheduler is in maintenance mode")
                while True:
                    row = self.connection.execute("SELECT * FROM queue_items WHERE (state='queued' AND run_at <= ?) OR (state='leased' AND lease_until <= ?) ORDER BY priority DESC, run_at ASC, task_id ASC LIMIT 1", (current, current)).fetchone()
                    if row is None:
                        # A previous loop may have atomically terminalized an
                        # expired item whose attempt ceiling was reached.
                        # Commit that fencing update before reporting an empty
                        # queue; rolling back here would reopen the crash loop.
                        self.connection.commit()
                        raise QueueEmpty("no queue item is ready")
                    if int(row["attempts"]) >= int(row["max_attempts"]) or int(row["execution_attempts"]) >= int(row["max_execution_attempts"]):
                        self.connection.execute("UPDATE queue_items SET state='failed', lease_owner=NULL, lease_until=NULL, lease_token=NULL, state_version=state_version+1 WHERE task_id=? AND state=? AND state_version=?", (row["task_id"], row["state"], row["state_version"]))
                        continue
                    break
                version = int(row["state_version"]) + 1
                until = current + lease_seconds
                token = str(uuid4())
                cursor = self.connection.execute("UPDATE queue_items SET state='leased', lease_owner=?, lease_until=?, lease_token=?, state_version=?, attempts=attempts+1, claim_count=claim_count+1 WHERE task_id=? AND state_version=? AND (state='queued' OR (state='leased' AND lease_until <= ?))", (worker_id, until, token, version, row["task_id"], row["state_version"], current))
                if cursor.rowcount != 1:
                    self.connection.rollback()
                    raise QueueEmpty("queue claim lost race")
                self.connection.commit()
            except Exception:
                if self.connection.in_transaction:
                    self.connection.rollback()
                raise
            return self.snapshot(row["task_id"])

    def set_maintenance(self, enabled: bool) -> None:
        with self._lock:
            self.connection.execute("INSERT INTO scheduler_control(id, maintenance) VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET maintenance=excluded.maintenance", (int(enabled),))
            self.connection.commit()

    def renew(self, task_id: str, *, worker_id: str, state_version: int, lease_seconds: float = 30.0) -> QueueItem:
        if not worker_id.strip() or lease_seconds <= 0:
            raise ValueError("worker_id and positive lease_seconds are required")
        with self._lock:
            now = time.time()
            cursor = self.connection.execute("UPDATE queue_items SET lease_until=? WHERE task_id=? AND state='leased' AND lease_owner=? AND state_version=? AND lease_until > ?", (now + lease_seconds, task_id, worker_id, state_version, now))
            self.connection.commit()
            if cursor.rowcount != 1:
                raise StaleLease(task_id)
            return self.snapshot(task_id)

    def assert_lease(self, task_id: str, *, worker_id: str, state_version: int, lease_token: str | None = None) -> None:
        with self._lock:
            query = "SELECT 1 FROM queue_items WHERE task_id=? AND state='leased' AND lease_owner=? AND state_version=? AND lease_until > ?"
            params: tuple[object, ...] = (task_id, worker_id, state_version, time.time())
            if lease_token is not None:
                query += " AND lease_token=?"
                params += (lease_token,)
            row = self.connection.execute(query, params).fetchone()
            if row is None:
                raise StaleLease(task_id)

    def assert_proof(self, proof: LeaseProof) -> None:
        with self._lock:
            assert_active_lease(self.connection, proof)

    def set_max_attempts(self, task_id: str, *, worker_id: str, state_version: int, max_attempts: int) -> QueueItem:
        """Bind a claimed queue item to the task's persisted retry ceiling."""
        self._validate_max_attempts(max_attempts)
        with self._lock:
            cursor = self.connection.execute("UPDATE queue_items SET max_attempts=? WHERE task_id=? AND state='leased' AND lease_owner=? AND state_version=? AND lease_until > ?", (max_attempts, task_id, worker_id, state_version, time.time()))
            self.connection.commit()
            if cursor.rowcount != 1:
                raise StaleLease(task_id)
            return self.snapshot(task_id)

    def set_max_execution_attempts(self, task_id: str, *, worker_id: str, state_version: int, max_attempts: int) -> QueueItem:
        """Bind the logical execution retry ceiling separately from lease claims."""
        self._validate_max_attempts(max_attempts)
        with self._lock:
            cursor = self.connection.execute("UPDATE queue_items SET max_execution_attempts=? WHERE task_id=? AND state='leased' AND lease_owner=? AND state_version=? AND lease_until > ?", (max_attempts, task_id, worker_id, state_version, time.time()))
            self.connection.commit()
            if cursor.rowcount != 1:
                raise StaleLease(task_id)
            return self.snapshot(task_id)

    def complete(self, task_id: str, *, worker_id: str, state_version: int, execution_attempt: bool = False) -> QueueItem:
        return self._finish(task_id, worker_id=worker_id, state_version=state_version, state="completed", execution_attempt=execution_attempt)

    def cancel(self, task_id: str, *, worker_id: str | None = None, state_version: int | None = None) -> QueueItem:
        """Terminalize a task as cancelled without turning it into a failure.

        A leased item must be cancelled by its current owner so the normal
        lease fence remains authoritative.  An unleased queued/waiting item
        may be cancelled by an operation or API process.  A running item is
        intentionally left alone when no owner proof is supplied; the worker
        will observe the durable cancellation request and publish the
        cancellation at its own execution boundary.
        """

        if worker_id is not None:
            if state_version is None:
                raise ValueError("state_version is required when worker_id is supplied")
            return self._finish(task_id, worker_id=worker_id, state_version=state_version, state="cancelled")
        if state_version is not None:
            raise ValueError("worker_id is required when state_version is supplied")
        with self._lock:
            row = self.connection.execute("SELECT state FROM queue_items WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if row["state"] in {"completed", "failed", "cancelled"}:
                return self.snapshot(task_id)
            if row["state"] not in {"queued", "waiting"}:
                # The owner still has to publish the result.  Do not bypass
                # its lease proof from an unrelated operation process.
                return self.snapshot(task_id)
            cursor = self.connection.execute(
                """UPDATE queue_items
                   SET state='cancelled', lease_owner=NULL, lease_until=NULL,
                       lease_token=NULL, wake_at=NULL, wake_reason=NULL,
                       state_version=state_version+1
                   WHERE task_id=? AND state IN ('queued', 'waiting')""",
                (task_id,),
            )
            self.connection.commit()
            if cursor.rowcount != 1:
                return self.snapshot(task_id)
            return self.snapshot(task_id)

    def fail(self, task_id: str, *, worker_id: str, state_version: int, retry: bool = False, max_attempts: int | None = None, execution_attempt: bool = False) -> QueueItem:
        if max_attempts is not None:
            self._validate_max_attempts(max_attempts)
        return self._finish(task_id, worker_id=worker_id, state_version=state_version, state="queued" if retry else "failed", max_attempts=max_attempts, execution_attempt=execution_attempt)

    def defer(self, task_id: str, *, worker_id: str, state_version: int) -> QueueItem:
        """Park a task that requires an external event before it can resume."""
        return self._finish(task_id, worker_id=worker_id, state_version=state_version, state="waiting")

    def defer_for_event(
        self,
        task_id: str,
        *,
        worker_id: str,
        state_version: int,
        reason: str,
    ) -> QueueItem:
        """Park a task until an explicitly named non-time wake event.

        ``wake_at`` remains NULL: clock passage alone cannot revive an event
        wait.  The owning maintenance/capacity authority must call
        :meth:`wake_waiting` with the same reason.
        """

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        with self._lock:
            cursor = self.connection.execute(
                """UPDATE queue_items
                   SET state='waiting', run_at=0, attempts=0,
                       lease_owner=NULL, lease_until=NULL, lease_token=NULL,
                       wake_at=NULL, wake_reason=?, state_version=state_version+1
                   WHERE task_id=? AND state='leased' AND lease_owner=?
                     AND state_version=? AND lease_until > ?""",
                (reason.strip(), task_id, worker_id, state_version, time.time()),
            )
            self.connection.commit()
            if cursor.rowcount != 1:
                raise StaleLease(task_id)
            return self.snapshot(task_id)

    def defer_until(
        self,
        task_id: str,
        *,
        worker_id: str,
        state_version: int,
        wake_at: datetime | float | int,
        reason: str,
    ) -> QueueItem:
        """Park a task until a durable, explicitly named wake boundary."""

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        wake_epoch = _epoch(wake_at)
        if not math.isfinite(wake_epoch):
            raise ValueError("wake_at must be finite")
        with self._lock:
            cursor = self.connection.execute(
                """UPDATE queue_items
                   SET state='waiting', run_at=?, attempts=0, lease_owner=NULL,
                       lease_until=NULL, lease_token=NULL,
                       wake_at=?, wake_reason=?, state_version=state_version+1
                   WHERE task_id=? AND state='leased' AND lease_owner=?
                     AND state_version=? AND lease_until > ?""",
                (wake_epoch, wake_epoch, reason.strip(), task_id, worker_id, state_version, time.time()),
            )
            self.connection.commit()
            if cursor.rowcount != 1:
                raise StaleLease(task_id)
            return self.snapshot(task_id)

    def wake(self, task_id: str, *, run_at: datetime | float | int | None = None) -> QueueItem:
        """Explicitly make a parked task claimable again."""
        with self._lock:
            cursor = self.connection.execute(
                "UPDATE queue_items SET state='queued', run_at=?, lease_owner=NULL, lease_until=NULL, lease_token=NULL, wake_at=NULL, wake_reason=NULL, state_version=state_version+1 WHERE task_id=? AND state='waiting'",
                (_epoch(run_at), task_id),
            )
            self.connection.commit()
            if cursor.rowcount != 1:
                raise ValueError(f"task is not waiting: {task_id}")
            return self.snapshot(task_id)

    def wake_waiting_task(self, task_id: str) -> int:
        """Wake one waiting task after its durable outcome is reconciled.

        This operation does not infer a retry and does not increment logical
        execution attempts.  The next claim resumes the existing Controller
        checkpoint through durable response replay.
        """

        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        with self._lock:
            cursor = self.connection.execute(
                """UPDATE queue_items
                   SET state='queued', run_at=?, attempts=0,
                       lease_owner=NULL, lease_until=NULL, lease_token=NULL,
                       wake_at=NULL, wake_reason=NULL, state_version=state_version+1
                   WHERE task_id=? AND state='waiting'""",
                (time.time(), task_id.strip()),
            )
            self.connection.commit()
            return cursor.rowcount

    def wake_waiting(self, *, reason: str) -> int:
        """Wake all event-waiting items for one durable wake authority."""

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        with self._lock:
            cursor = self.connection.execute(
                """UPDATE queue_items
                   SET state='queued', run_at=?, wake_at=NULL, wake_reason=NULL,
                       state_version=state_version+1
                   WHERE state='waiting' AND wake_reason=?""",
                (time.time(), reason.strip()),
            )
            self.connection.commit()
            return cursor.rowcount

    def wake_waiting_prefix(self, prefix: str) -> int:
        """Wake event-waiting items for a family of lane-specific reasons."""

        if not isinstance(prefix, str) or not prefix.strip():
            raise ValueError("prefix must be a non-empty string")
        with self._lock:
            cursor = self.connection.execute(
                """UPDATE queue_items
                   SET state='queued', run_at=?, wake_at=NULL, wake_reason=NULL,
                       state_version=state_version+1
                   WHERE state='waiting' AND wake_reason LIKE ?""",
                (time.time(), f"{prefix.strip()}%"),
            )
            self.connection.commit()
            return cursor.rowcount

    def wake_due(
        self,
        *,
        now: datetime | float | int | None = None,
        reason: str,
    ) -> int:
        """Wake only waiting items whose named external boundary is due."""

        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        current = _epoch(now)
        with self._lock:
            cursor = self.connection.execute(
                """UPDATE queue_items
                   SET state='queued', run_at=?, wake_at=NULL, wake_reason=NULL,
                       state_version=state_version+1
                   WHERE state='waiting' AND wake_reason=? AND wake_at IS NOT NULL
                     AND wake_at <= ?""",
                (current, reason.strip(), current),
            )
            self.connection.commit()
            return cursor.rowcount

    def _finish(self, task_id: str, *, worker_id: str, state_version: int, state: str, max_attempts: int | None = None, execution_attempt: bool = False) -> QueueItem:
        with self._lock:
            if state == "queued":
                row = self.connection.execute("SELECT attempts, max_attempts, execution_attempts, max_execution_attempts FROM queue_items WHERE task_id=? AND state='leased' AND lease_owner=? AND state_version=? AND lease_until > ?", (task_id, worker_id, state_version, time.time())).fetchone()
                if row is None:
                    self.connection.rollback()
                    raise StaleLease(task_id)
                attempt_limit = int(row["max_attempts"])
                if max_attempts is not None:
                    attempt_limit = min(attempt_limit, max_attempts)
                next_execution_attempts = int(row["execution_attempts"]) + (1 if execution_attempt else 0)
                if int(row["attempts"]) >= attempt_limit or next_execution_attempts >= int(row["max_execution_attempts"]):
                    state = "failed"
            execution_increment = 1 if execution_attempt else 0
            claim_streak_reset = "attempts=0, " if state == "waiting" else ""
            cursor = self.connection.execute(f"UPDATE queue_items SET state=?, execution_attempts=execution_attempts+?, {claim_streak_reset}lease_owner=NULL, lease_until=NULL, lease_token=NULL, wake_at=NULL, wake_reason=NULL, state_version=state_version+1 WHERE task_id=? AND state='leased' AND lease_owner=? AND state_version=? AND lease_until > ?", (state, execution_increment, task_id, worker_id, state_version, time.time()))
            self.connection.commit()
            if cursor.rowcount != 1:
                raise StaleLease(task_id)
            return self.snapshot(task_id)

    def reap_expired(self, *, now: datetime | float | int | None = None) -> int:
        current = _epoch(now)
        with self._lock:
            cursor = self.connection.execute("UPDATE queue_items SET state=CASE WHEN attempts >= max_attempts OR execution_attempts >= max_execution_attempts THEN 'failed' ELSE 'queued' END, lease_owner=NULL, lease_until=NULL, lease_token=NULL, state_version=state_version+1 WHERE state='leased' AND lease_until <= ?", (current,))
            self.connection.commit()
            return cursor.rowcount

    def snapshot(self, task_id: str) -> QueueItem:
        with self._lock:
            row = self.connection.execute("SELECT * FROM queue_items WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            return self._item(row)
