"""SQLite state store for Phase 3 durable resume and idempotency."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from ..domain.protocol import Event, Step, Task, ToolResult


class SQLiteStateStore:
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
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(checkpoints)")}
        if "state_payload" not in columns:
            self.connection.execute("ALTER TABLE checkpoints ADD COLUMN state_payload TEXT NOT NULL DEFAULT '{}'")
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

    def _rows(self, table: str, column: str = "payload") -> list[dict[str, Any]]:
        return [json.loads(row[column]) for row in self.connection.execute(f"SELECT {column} FROM {table}").fetchall()]

    def snapshot(self) -> dict[str, Any]:
        tasks = {item["task_id"]: item for item in self._rows("tasks")}
        steps = {item["step_id"]: item for item in self._rows("steps")}
        results = {item["call_id"]: item for item in self._rows("tool_results")}
        events = self._rows("events")
        checkpoints = [dict(row) | {"state": json.loads(row["state_payload"])} for row in self.connection.execute("SELECT task_id, step_id, phase, state_payload FROM checkpoints ORDER BY sequence").fetchall()]
        return {"tasks": tasks, "steps": steps, "tool_results": results, "events": events, "checkpoints": checkpoints}
