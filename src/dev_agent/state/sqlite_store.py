"""SQLite state store for Phase 3 durable resume and idempotency."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from ..domain.protocol import Event, Step, Task, ToolResult


class SQLiteStateStore:
    SCHEMA_VERSION = 2

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS steps (step_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tool_results (call_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS checkpoints (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, step_id TEXT NOT NULL, phase TEXT NOT NULL, state_payload TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS idempotency (idempotency_key TEXT PRIMARY KEY, result_payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS approvals (approval_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, side_effect_level TEXT NOT NULL, actor TEXT NOT NULL, call_id TEXT NOT NULL, arguments_hash TEXT NOT NULL, expires_at REAL, revoked INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS effect_intents (idempotency_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, tool_name TEXT NOT NULL, arguments_payload TEXT NOT NULL, status TEXT NOT NULL, result_payload TEXT);
            CREATE TABLE IF NOT EXISTS approval_consumptions (approval_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(checkpoints)")}
        if "state_payload" not in columns:
            self.connection.execute("ALTER TABLE checkpoints ADD COLUMN state_payload TEXT NOT NULL DEFAULT '{}'")
        approval_columns = {row[1] for row in self.connection.execute("PRAGMA table_info(approvals)")}
        for name in ("call_id", "arguments_hash"):
            if name not in approval_columns:
                self.connection.execute(f"ALTER TABLE approvals ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
        if "expires_at" not in approval_columns:
            self.connection.execute("ALTER TABLE approvals ADD COLUMN expires_at REAL")
        if "revoked" not in approval_columns:
            self.connection.execute("ALTER TABLE approvals ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0")
        self.connection.execute("INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '2')")
        current = int(self.connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0])
        if current > self.SCHEMA_VERSION:
            raise ValueError(f"unsupported state schema version: {current}")
        if current < self.SCHEMA_VERSION:
            self.connection.execute("UPDATE schema_meta SET value = ? WHERE key = 'schema_version'", (str(self.SCHEMA_VERSION),))
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SQLiteStateStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def save_task(self, task: Task) -> None:
        self.connection.execute("INSERT OR REPLACE INTO tasks VALUES (?, ?)", (task.task_id, json.dumps(task.to_dict(), ensure_ascii=False)))
        self.connection.commit()

    def save_step(self, step: Step) -> None:
        self.connection.execute("INSERT OR REPLACE INTO steps VALUES (?, ?)", (step.step_id, json.dumps(step.to_dict(), ensure_ascii=False)))
        self.connection.commit()

    def save_tool_result(self, result: ToolResult) -> None:
        self.connection.execute("INSERT OR REPLACE INTO tool_results VALUES (?, ?)", (result.call_id, json.dumps(result.to_dict(), ensure_ascii=False)))
        self.connection.commit()

    def append_event(self, event: Event) -> None:
        self.connection.execute("INSERT OR REPLACE INTO events(event_id, payload) VALUES (?, ?)", (event.event_id, json.dumps(event.to_dict(), ensure_ascii=False)))
        self.connection.commit()

    def checkpoint(self, *, task_id: str, step_id: str, phase: str, state: dict[str, Any]) -> None:
        self.connection.execute("INSERT INTO checkpoints(task_id, step_id, phase, state_payload) VALUES (?, ?, ?, ?)", (task_id, step_id, phase, json.dumps(state, ensure_ascii=False)))
        self.connection.commit()

    def load_latest_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT task_id, step_id, phase, state_payload FROM checkpoints WHERE task_id = ? ORDER BY sequence DESC LIMIT 1", (task_id,)).fetchone()
        if row is None:
            return None
        return {"task_id": row["task_id"], "step_id": row["step_id"], "phase": row["phase"], "state": json.loads(row["state_payload"])}

    def load_task(self, task_id: str) -> Task | None:
        row = self.connection.execute("SELECT payload FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return Task.from_dict(json.loads(row["payload"])) if row else None

    def get_idempotent(self, key: str) -> ToolResult | None:
        row = self.connection.execute("SELECT result_payload FROM idempotency WHERE idempotency_key = ?", (key,)).fetchone()
        return ToolResult.from_dict(json.loads(row["result_payload"])) if row else None

    def save_idempotent(self, key: str, result: ToolResult) -> None:
        self.connection.execute("INSERT OR IGNORE INTO idempotency VALUES (?, ?)", (key, json.dumps(result.to_dict(), ensure_ascii=False)))
        self.connection.commit()

    def save_approval(self, approval_id: str, *, task_id: str, side_effect_level: str, actor: str, call_id: str, arguments_hash: str, expires_at: float | None = None) -> None:
        self.connection.execute("INSERT INTO approvals(approval_id, task_id, side_effect_level, actor, call_id, arguments_hash, expires_at, revoked) VALUES (?, ?, ?, ?, ?, ?, ?, 0)", (approval_id, task_id, side_effect_level, actor, call_id, arguments_hash, expires_at))
        self.connection.commit()

    def has_approval(self, approval_id: str, *, task_id: str, side_effect_level: str, call_id: str, arguments_hash: str) -> bool:
        row = self.connection.execute("SELECT 1 FROM approvals WHERE approval_id = ? AND task_id = ? AND side_effect_level = ? AND call_id = ? AND arguments_hash = ? AND revoked = 0 AND (expires_at IS NULL OR expires_at > ?)", (approval_id, task_id, side_effect_level, call_id, arguments_hash, time.time())).fetchone()
        return row is not None

    def revoke_approval(self, approval_id: str) -> None:
        cursor = self.connection.execute("UPDATE approvals SET revoked = 1 WHERE approval_id = ?", (approval_id,))
        self.connection.commit()
        if cursor.rowcount != 1:
            raise KeyError(approval_id)

    def consume_approval(self, approval_id: str, *, task_id: str, side_effect_level: str, call_id: str, arguments_hash: str) -> bool:
        if not self.has_approval(approval_id, task_id=task_id, side_effect_level=side_effect_level, call_id=call_id, arguments_hash=arguments_hash):
            return False
        cursor = self.connection.execute("INSERT OR IGNORE INTO approval_consumptions(approval_id) VALUES (?)", (approval_id,))
        self.connection.commit()
        return cursor.rowcount == 1

    def get_effect_intent(self, key: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM effect_intents WHERE idempotency_key = ?", (key,)).fetchone()
        if row is None:
            return None
        return {"idempotency_key": row["idempotency_key"], "task_id": row["task_id"], "tool_name": row["tool_name"], "arguments": json.loads(row["arguments_payload"]), "status": row["status"], "result": json.loads(row["result_payload"]) if row["result_payload"] else None}

    def create_effect_intent(self, key: str, *, task_id: str, tool_name: str, arguments: dict[str, Any]) -> bool:
        cursor = self.connection.execute("INSERT OR IGNORE INTO effect_intents(idempotency_key, task_id, tool_name, arguments_payload, status) VALUES (?, ?, ?, ?, 'pending')", (key, task_id, tool_name, json.dumps(arguments, ensure_ascii=False)))
        self.connection.commit()
        return cursor.rowcount == 1

    def complete_effect_intent(self, key: str, result: ToolResult) -> None:
        cursor = self.connection.execute("UPDATE effect_intents SET status = 'succeeded', result_payload = ? WHERE idempotency_key = ?", (json.dumps(result.to_dict(), ensure_ascii=False), key))
        self.connection.commit()
        if cursor.rowcount != 1:
            raise ValueError(f"effect intent not found: {key}")

    def mark_effect_unknown(self, key: str, *, reason: str) -> None:
        cursor = self.connection.execute("UPDATE effect_intents SET status = 'unknown', result_payload = ? WHERE idempotency_key = ?", (json.dumps({"unknown": True, "reason": reason}, ensure_ascii=False), key))
        self.connection.commit()
        if cursor.rowcount != 1:
            raise ValueError(f"effect intent not found: {key}")

    def _rows(self, table: str, column: str = "payload") -> list[dict[str, Any]]:
        return [json.loads(row[column]) for row in self.connection.execute(f"SELECT {column} FROM {table}").fetchall()]

    def snapshot(self) -> dict[str, Any]:
        tasks = {item["task_id"]: item for item in self._rows("tasks")}
        steps = {item["step_id"]: item for item in self._rows("steps")}
        results = {item["call_id"]: item for item in self._rows("tool_results")}
        events = self._rows("events")
        checkpoints = [dict(row) | {"state": json.loads(row["state_payload"])} for row in self.connection.execute("SELECT task_id, step_id, phase, state_payload FROM checkpoints ORDER BY sequence").fetchall()]
        return {"tasks": tasks, "steps": steps, "tool_results": results, "events": events, "checkpoints": checkpoints}
