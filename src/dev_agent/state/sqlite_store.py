"""SQLite state store for Phase 3 durable resume and idempotency."""

from __future__ import annotations

import json
from functools import wraps
from pathlib import Path
import sqlite3
from threading import RLock
import time
from typing import Any

from ..domain.protocol import Event, Step, Task, ToolResult


def _serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


class SQLiteStateStore:
    SCHEMA_VERSION = 5
    _EFFECT_TRANSITIONS = {
        "pending": {"prepared", "dispatching", "unknown", "succeeded", "reconciling"},
        "prepared": {"dispatching", "unknown", "confirmed_failed", "reconciling"},
        "dispatching": {"unknown", "succeeded", "confirmed_failed", "reconciling"},
        "unknown": {"reconciling", "succeeded", "confirmed_failed", "reconciled"},
        "reconciling": {"unknown", "succeeded", "confirmed_failed", "reconciled"},
        "succeeded": set(),
        "confirmed_failed": set(),
        "reconciled": set(),
    }

    _V1_SCHEMA = """
        CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS steps (step_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tool_results (call_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS checkpoints (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, step_id TEXT NOT NULL, phase TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS idempotency (idempotency_key TEXT PRIMARY KEY, result_payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals (approval_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, side_effect_level TEXT NOT NULL, actor TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """

    _LATEST_SCHEMA = """
        CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS steps (step_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tool_results (call_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS checkpoints (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, step_id TEXT NOT NULL, phase TEXT NOT NULL, state_payload TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS idempotency (idempotency_key TEXT PRIMARY KEY, result_payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals (approval_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, side_effect_level TEXT NOT NULL, actor TEXT NOT NULL, call_id TEXT NOT NULL, arguments_hash TEXT NOT NULL, expires_at REAL, revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS effect_intents (idempotency_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, tool_name TEXT NOT NULL, arguments_payload TEXT NOT NULL, status TEXT NOT NULL, result_payload TEXT);
        CREATE TABLE IF NOT EXISTS approval_consumptions (approval_id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS effect_reconciliations (sequence INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT NOT NULL, status TEXT NOT NULL, actor TEXT NOT NULL, source TEXT NOT NULL, external_id TEXT, evidence_payload TEXT NOT NULL, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS provider_dispatch_audits (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, request_id TEXT NOT NULL, intent_key TEXT, provider_id TEXT NOT NULL, resource_id TEXT NOT NULL, native_unit TEXT NOT NULL, estimated_cost_minor INTEGER, price_currency TEXT, outcome TEXT NOT NULL, details_payload TEXT NOT NULL DEFAULT '{}', recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_provider_dispatch_audits_task ON provider_dispatch_audits(task_id, sequence);
        CREATE INDEX IF NOT EXISTS idx_provider_dispatch_audits_request ON provider_dispatch_audits(request_id, sequence);
        CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Controller.cancel may be called by an API/UI thread while the run
        # loop is executing.  All durable mutations still pass through the
        # store's transactions; allowing the connection across threads avoids
        # a Python-only thread-affinity failure at that boundary.
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        try:
            user_tables = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
            if not user_tables:
                # Fresh databases are created at the latest schema directly;
                # only existing databases pass through ordered migrations.
                self.connection.executescript(self._LATEST_SCHEMA)
                self.connection.execute("INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)", (str(self.SCHEMA_VERSION),))
                self.connection.commit()
                return
            self.connection.executescript(self._V1_SCHEMA)
            self.connection.execute("INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '1')")
            current = int(self.connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0])
            if current > self.SCHEMA_VERSION:
                raise ValueError(f"unsupported state schema version: {current}")
            self.connection.commit()
            self.connection.execute("BEGIN")
            if current < 2:
                columns = {row[1] for row in self.connection.execute("PRAGMA table_info(checkpoints)")}
                if "state_payload" not in columns:
                    self.connection.execute("ALTER TABLE checkpoints ADD COLUMN state_payload TEXT NOT NULL DEFAULT '{}'")
                approval_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(approvals)")}
                for name in ("call_id", "arguments_hash"):
                    if name not in approval_columns:
                        self.connection.execute(f"ALTER TABLE approvals ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
                self.connection.execute("CREATE TABLE IF NOT EXISTS effect_intents (idempotency_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, tool_name TEXT NOT NULL, arguments_payload TEXT NOT NULL, status TEXT NOT NULL, result_payload TEXT)")
                self.connection.execute("UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'")
                current = 2
            if current < 3:
                approval_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(approvals)")}
                if "expires_at" not in approval_columns:
                    self.connection.execute("ALTER TABLE approvals ADD COLUMN expires_at REAL")
                if "revoked" not in approval_columns:
                    self.connection.execute("ALTER TABLE approvals ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0")
                self.connection.execute("CREATE TABLE IF NOT EXISTS approval_consumptions (approval_id TEXT PRIMARY KEY)")
                self.connection.execute("UPDATE schema_meta SET value = '3' WHERE key = 'schema_version'")
                current = 3
            if current < 4:
                self.connection.execute("CREATE TABLE IF NOT EXISTS effect_reconciliations (sequence INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT NOT NULL, status TEXT NOT NULL, actor TEXT NOT NULL, source TEXT NOT NULL, external_id TEXT, evidence_payload TEXT NOT NULL, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                self.connection.execute("UPDATE schema_meta SET value = '4' WHERE key = 'schema_version'")
                current = 4
            if current < 5:
                self.connection.execute("CREATE TABLE IF NOT EXISTS provider_dispatch_audits (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, request_id TEXT NOT NULL, intent_key TEXT, provider_id TEXT NOT NULL, resource_id TEXT NOT NULL, native_unit TEXT NOT NULL, estimated_cost_minor INTEGER, price_currency TEXT, outcome TEXT NOT NULL, details_payload TEXT NOT NULL DEFAULT '{}', recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                self.connection.execute("CREATE INDEX IF NOT EXISTS idx_provider_dispatch_audits_task ON provider_dispatch_audits(task_id, sequence)")
                self.connection.execute("CREATE INDEX IF NOT EXISTS idx_provider_dispatch_audits_request ON provider_dispatch_audits(request_id, sequence)")
                self.connection.execute("UPDATE schema_meta SET value = '5' WHERE key = 'schema_version'")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    @_serialized
    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SQLiteStateStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @_serialized
    def save_task(self, task: Task) -> None:
        self.connection.execute("INSERT OR REPLACE INTO tasks VALUES (?, ?)", (task.task_id, json.dumps(task.to_dict(), ensure_ascii=False)))
        self.connection.commit()

    @_serialized
    def save_step(self, step: Step) -> None:
        self.connection.execute("INSERT OR REPLACE INTO steps VALUES (?, ?)", (step.step_id, json.dumps(step.to_dict(), ensure_ascii=False)))
        self.connection.commit()

    @_serialized
    def save_tool_result(self, result: ToolResult) -> None:
        self.connection.execute("INSERT OR REPLACE INTO tool_results VALUES (?, ?)", (result.call_id, json.dumps(result.to_dict(), ensure_ascii=False)))
        self.connection.commit()

    @_serialized
    def append_event(self, event: Event) -> None:
        self.connection.execute("INSERT INTO events(event_id, payload) VALUES (?, ?)", (event.event_id, json.dumps(event.to_dict(), ensure_ascii=False)))
        self.connection.commit()

    @_serialized
    def checkpoint(self, *, task_id: str, step_id: str, phase: str, state: dict[str, Any]) -> None:
        self.connection.execute("INSERT INTO checkpoints(task_id, step_id, phase, state_payload) VALUES (?, ?, ?, ?)", (task_id, step_id, phase, json.dumps(state, ensure_ascii=False)))
        self.connection.commit()

    @_serialized
    def load_latest_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT task_id, step_id, phase, state_payload FROM checkpoints WHERE task_id = ? ORDER BY sequence DESC LIMIT 1", (task_id,)).fetchone()
        if row is None:
            return None
        return {"task_id": row["task_id"], "step_id": row["step_id"], "phase": row["phase"], "state": json.loads(row["state_payload"])}

    @_serialized
    def load_task(self, task_id: str) -> Task | None:
        row = self.connection.execute("SELECT payload FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return Task.from_persisted_dict(json.loads(row["payload"])) if row else None

    @_serialized
    def get_idempotent(self, key: str) -> ToolResult | None:
        row = self.connection.execute("SELECT result_payload FROM idempotency WHERE idempotency_key = ?", (key,)).fetchone()
        return ToolResult.from_dict(json.loads(row["result_payload"])) if row else None

    @_serialized
    def save_idempotent(self, key: str, result: ToolResult) -> None:
        self.connection.execute("INSERT OR IGNORE INTO idempotency VALUES (?, ?)", (key, json.dumps(result.to_dict(), ensure_ascii=False)))
        self.connection.commit()

    @_serialized
    def save_approval(self, approval_id: str, *, task_id: str, side_effect_level: str, actor: str, call_id: str, arguments_hash: str, expires_at: float | None = None) -> None:
        try:
            self.connection.execute("INSERT INTO approvals(approval_id, task_id, side_effect_level, actor, call_id, arguments_hash, expires_at, revoked) VALUES (?, ?, ?, ?, ?, ?, ?, 0)", (approval_id, task_id, side_effect_level, actor, call_id, arguments_hash, expires_at))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    @_serialized
    def has_approval(self, approval_id: str, *, task_id: str, side_effect_level: str, call_id: str, arguments_hash: str) -> bool:
        row = self.connection.execute("SELECT 1 FROM approvals WHERE approval_id = ? AND task_id = ? AND side_effect_level = ? AND call_id = ? AND arguments_hash = ? AND revoked = 0 AND (expires_at IS NULL OR expires_at > ?)", (approval_id, task_id, side_effect_level, call_id, arguments_hash, time.time())).fetchone()
        return row is not None

    @_serialized
    def revoke_approval(self, approval_id: str) -> None:
        cursor = self.connection.execute("UPDATE approvals SET revoked = 1 WHERE approval_id = ?", (approval_id,))
        self.connection.commit()
        if cursor.rowcount != 1:
            raise KeyError(approval_id)

    @_serialized
    def consume_approval(self, approval_id: str, *, task_id: str, side_effect_level: str, call_id: str, arguments_hash: str) -> bool:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute("SELECT 1 FROM approvals WHERE approval_id = ? AND task_id = ? AND side_effect_level = ? AND call_id = ? AND arguments_hash = ? AND revoked = 0 AND (expires_at IS NULL OR expires_at > ?)", (approval_id, task_id, side_effect_level, call_id, arguments_hash, time.time())).fetchone()
            if row is None:
                self.connection.rollback()
                return False
            cursor = self.connection.execute("INSERT OR IGNORE INTO approval_consumptions(approval_id) VALUES (?)", (approval_id,))
            self.connection.commit()
            return cursor.rowcount == 1
        except Exception:
            self.connection.rollback()
            raise

    @_serialized
    def get_effect_intent(self, key: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM effect_intents WHERE idempotency_key = ?", (key,)).fetchone()
        if row is None:
            return None
        return {"idempotency_key": row["idempotency_key"], "task_id": row["task_id"], "tool_name": row["tool_name"], "arguments": json.loads(row["arguments_payload"]), "status": row["status"], "result": json.loads(row["result_payload"]) if row["result_payload"] else None}

    @_serialized
    def create_effect_intent(self, key: str, *, task_id: str, tool_name: str, arguments: dict[str, Any]) -> bool:
        cursor = self.connection.execute("INSERT OR IGNORE INTO effect_intents(idempotency_key, task_id, tool_name, arguments_payload, status) VALUES (?, ?, ?, ?, 'pending')", (key, task_id, tool_name, json.dumps(arguments, ensure_ascii=False)))
        self.connection.commit()
        return cursor.rowcount == 1

    @_serialized
    def complete_effect_intent(self, key: str, result: ToolResult) -> None:
        self.transition_effect_intent(key, to_status="succeeded", result=result.to_dict())

    @_serialized
    def mark_effect_unknown(self, key: str, *, reason: str) -> None:
        self.transition_effect_intent(key, to_status="unknown", result={"unknown": True, "reason": reason})

    @_serialized
    def transition_effect_intent(self, key: str, *, to_status: str, result: dict[str, Any] | None = None, lease_proof: Any | None = None) -> None:
        allowed = {"prepared", "dispatching", "unknown", "succeeded", "confirmed_failed", "reconciling", "reconciled"}
        if to_status not in allowed:
            raise ValueError(f"invalid effect intent status: {to_status}")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if lease_proof is not None and self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='queue_items'").fetchone() is not None:
                row = self.connection.execute("SELECT 1 FROM queue_items WHERE task_id=? AND state='leased' AND lease_owner=? AND lease_token=? AND state_version=? AND lease_until > ?", (lease_proof.task_id, lease_proof.worker_id, lease_proof.lease_token, lease_proof.state_version, time.time())).fetchone()
                if row is None:
                    from ..scheduler.queue import StaleLease
                    raise StaleLease(lease_proof.task_id)
            row = self.connection.execute("SELECT status FROM effect_intents WHERE idempotency_key = ?", (key,)).fetchone()
            if row is None:
                raise ValueError(f"effect intent not found: {key}")
            current = row["status"]
            if to_status != current and to_status not in self._EFFECT_TRANSITIONS.get(current, set()):
                raise ValueError(f"invalid effect intent transition: {current} -> {to_status}")
            payload = json.dumps(result, ensure_ascii=False) if result is not None else None
            self.connection.execute("UPDATE effect_intents SET status = ?, result_payload = COALESCE(?, result_payload) WHERE idempotency_key = ?", (to_status, payload, key))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    @_serialized
    def record_provider_audit(self, *, task_id: str, request_id: str, intent_key: str | None, provider_id: str, resource_id: str, native_unit: str, estimated_cost_minor: int | None, price_currency: str | None, outcome: str, details: dict[str, Any] | None = None) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (task_id, request_id, provider_id, resource_id, native_unit, outcome)):
            raise ValueError("provider audit identity and outcome are required")
        if estimated_cost_minor is not None and (isinstance(estimated_cost_minor, bool) or not isinstance(estimated_cost_minor, int) or estimated_cost_minor < 0):
            raise ValueError("estimated_cost_minor must be a non-negative integer or None")
        self.connection.execute(
            "INSERT INTO provider_dispatch_audits(task_id, request_id, intent_key, provider_id, resource_id, native_unit, estimated_cost_minor, price_currency, outcome, details_payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, request_id, intent_key, provider_id, resource_id, native_unit, estimated_cost_minor, price_currency, outcome, json.dumps(details or {}, ensure_ascii=False)),
        )
        self.connection.commit()

    @_serialized
    def list_provider_audits(self, *, task_id: str | None = None, request_id: str | None = None) -> list[dict[str, Any]]:
        clauses = []
        values: list[str] = []
        if task_id is not None:
            clauses.append("task_id = ?")
            values.append(task_id)
        if request_id is not None:
            clauses.append("request_id = ?")
            values.append(request_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.connection.execute(f"SELECT sequence, task_id, request_id, intent_key, provider_id, resource_id, native_unit, estimated_cost_minor, price_currency, outcome, details_payload, recorded_at FROM provider_dispatch_audits{where} ORDER BY sequence", values).fetchall()
        return [dict(row) | {"details": json.loads(row["details_payload"])} for row in rows]

    @_serialized
    def commit_transition(self, *, task: Task | None = None, step: Step | None = None, checkpoint: dict[str, Any] | None = None, event: Event | None = None, events: list[Event] | None = None, tool_result: ToolResult | None = None, lease_proof: Any | None = None) -> None:
        """Atomically persist the records belonging to one runtime transition."""
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if lease_proof is not None and self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='queue_items'").fetchone() is not None:
                row = self.connection.execute("SELECT 1 FROM queue_items WHERE task_id=? AND state='leased' AND lease_owner=? AND lease_token=? AND state_version=? AND lease_until > ?", (lease_proof.task_id, lease_proof.worker_id, lease_proof.lease_token, lease_proof.state_version, time.time())).fetchone()
                if row is None:
                    from ..scheduler.queue import StaleLease
                    raise StaleLease(lease_proof.task_id)
            if task is not None:
                self.connection.execute("INSERT OR REPLACE INTO tasks VALUES (?, ?)", (task.task_id, json.dumps(task.to_dict(), ensure_ascii=False)))
            if step is not None:
                self.connection.execute("INSERT OR REPLACE INTO steps VALUES (?, ?)", (step.step_id, json.dumps(step.to_dict(), ensure_ascii=False)))
            if checkpoint is not None:
                self.connection.execute("INSERT INTO checkpoints(task_id, step_id, phase, state_payload) VALUES (?, ?, ?, ?)", (checkpoint["task_id"], checkpoint["step_id"], checkpoint["phase"], json.dumps(checkpoint.get("state", {}), ensure_ascii=False)))
            if tool_result is not None:
                self.connection.execute("INSERT OR REPLACE INTO tool_results VALUES (?, ?)", (tool_result.call_id, json.dumps(tool_result.to_dict(), ensure_ascii=False)))
            transition_events = list(events or [])
            if event is not None:
                transition_events.append(event)
            for transition_event in transition_events:
                self.connection.execute("INSERT INTO events(event_id, payload) VALUES (?, ?)", (transition_event.event_id, json.dumps(transition_event.to_dict(), ensure_ascii=False)))
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        # Keep the historical checkpoint override as a post-commit crash
        # injection seam.  Production uses the atomic write above; tests that
        # model a process dying immediately after a durable boundary can still
        # override ``checkpoint`` without weakening that transaction.
        if checkpoint is not None and type(self).checkpoint is not SQLiteStateStore.checkpoint:
            self.checkpoint(task_id=checkpoint["task_id"], step_id=checkpoint["step_id"], phase=checkpoint["phase"], state=checkpoint.get("state", {}))

    @_serialized
    def reconcile_effect_intent(self, key: str, *, status: str, actor: str, source: str, external_id: str | None = None, evidence: dict[str, Any] | None = None, result: ToolResult | None = None) -> None:
        if status not in {"succeeded", "confirmed_failed", "unknown"}:
            raise ValueError(f"invalid reconciliation status: {status}")
        if not actor.strip() or not source.strip():
            raise ValueError("reconciliation actor and source are required")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute("SELECT status FROM effect_intents WHERE idempotency_key = ?", (key,)).fetchone()
            if row is None:
                raise ValueError(f"effect intent not found: {key}")
            current = row["status"]
            if current != "reconciling" and "reconciling" not in self._EFFECT_TRANSITIONS.get(current, set()):
                raise ValueError(f"invalid effect intent transition: {current} -> reconciling")
            if status != "reconciling" and status not in self._EFFECT_TRANSITIONS["reconciling"] and status != current:
                raise ValueError(f"invalid effect intent transition: reconciling -> {status}")
            audit = {"actor": actor, "source": source, "external_id": external_id, "evidence": evidence or {}}
            payload = result.to_dict() if result is not None else audit
            self.connection.execute("INSERT INTO effect_reconciliations(idempotency_key, status, actor, source, external_id, evidence_payload) VALUES (?, ?, ?, ?, ?, ?)", (key, status, actor, source, external_id, json.dumps(evidence or {}, ensure_ascii=False)))
            self.connection.execute("UPDATE effect_intents SET status = 'reconciling' WHERE idempotency_key = ?", (key,))
            self.connection.execute("UPDATE effect_intents SET status = ?, result_payload = ? WHERE idempotency_key = ?", (status, json.dumps(payload, ensure_ascii=False), key))
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    def _rows(self, table: str, column: str = "payload") -> list[dict[str, Any]]:
        return [json.loads(row[column]) for row in self.connection.execute(f"SELECT {column} FROM {table}").fetchall()]

    @_serialized
    def snapshot(self) -> dict[str, Any]:
        tasks = {item["task_id"]: item for item in self._rows("tasks")}
        steps = {item["step_id"]: item for item in self._rows("steps")}
        results = {item["call_id"]: item for item in self._rows("tool_results")}
        events = self._rows("events")
        checkpoints = [dict(row) | {"state": json.loads(row["state_payload"])} for row in self.connection.execute("SELECT task_id, step_id, phase, state_payload FROM checkpoints ORDER BY sequence").fetchall()]
        return {"tasks": tasks, "steps": steps, "tool_results": results, "events": events, "checkpoints": checkpoints}

    @_serialized
    def has_event(self, task_id: str, event_type: str) -> bool:
        rows = self.connection.execute("SELECT payload FROM events WHERE payload LIKE ?", (f'%"task_id": "{task_id}"%',)).fetchall()
        return any(json.loads(row["payload"]).get("event_type") == event_type for row in rows)
