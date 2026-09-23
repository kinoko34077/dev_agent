"""Core task-state SQL operations for the SQLite state facade."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..domain.protocol import Event, Step, Task, ToolResult
from ..human import HumanRequest, HumanResponse


class CoreStateRepository:
    """Execute core state SQL without owning locks or transactions."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save_task(self, task: Task) -> None:
        self.connection.execute("INSERT OR REPLACE INTO tasks VALUES (?, ?)", (task.task_id, json.dumps(task.to_dict(), ensure_ascii=False)))

    def save_step(self, step: Step) -> None:
        self.connection.execute("INSERT OR REPLACE INTO steps VALUES (?, ?)", (step.step_id, json.dumps(step.to_dict(), ensure_ascii=False)))

    def save_tool_result(self, result: ToolResult) -> None:
        self.connection.execute("INSERT OR REPLACE INTO tool_results VALUES (?, ?)", (result.call_id, json.dumps(result.to_dict(), ensure_ascii=False)))

    def append_event(self, event: Event) -> None:
        self.connection.execute("INSERT INTO events(event_id, payload) VALUES (?, ?)", (event.event_id, json.dumps(event.to_dict(), ensure_ascii=False)))

    def append_event_if_absent(self, event: Event) -> bool:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO events(event_id, payload) VALUES (?, ?)",
            (event.event_id, json.dumps(event.to_dict(), ensure_ascii=False)),
        )
        return cursor.rowcount == 1

    def checkpoint(self, *, task_id: str, step_id: str, phase: str, state: dict[str, Any]) -> None:
        self.connection.execute("INSERT INTO checkpoints(task_id, step_id, phase, state_payload) VALUES (?, ?, ?, ?)", (task_id, step_id, phase, json.dumps(state, ensure_ascii=False)))

    def load_latest_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT task_id, step_id, phase, state_payload FROM checkpoints WHERE task_id = ? ORDER BY sequence DESC LIMIT 1", (task_id,)).fetchone()
        if row is None:
            return None
        return {"task_id": row["task_id"], "step_id": row["step_id"], "phase": row["phase"], "state": json.loads(row["state_payload"])}

    def load_task(self, task_id: str) -> Task | None:
        row = self.connection.execute("SELECT payload FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return Task.from_persisted_dict(json.loads(row["payload"])) if row else None

    def save_human_request(self, request: HumanRequest) -> None:
        payload = json.dumps(request.to_dict(), ensure_ascii=False, separators=(",", ":"))
        existing = self.connection.execute(
            "SELECT request_payload FROM human_requests WHERE request_id = ?",
            (request.request_id,),
        ).fetchone()
        if existing is not None:
            if existing["request_payload"] != payload:
                raise ValueError(f"human request already exists with different payload: {request.request_id}")
            return
        self.connection.execute(
            "INSERT INTO human_requests(request_id, task_id, status, request_payload, requested_at) VALUES (?, ?, 'pending', ?, ?)",
            (request.request_id, request.task_id, payload, request.created_at),
        )

    def get_human_request(self, request_id: str) -> HumanRequest | None:
        row = self.connection.execute(
            "SELECT request_payload FROM human_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        return HumanRequest.from_dict(json.loads(row["request_payload"])) if row else None

    def list_pending_human_requests(self) -> list[HumanRequest]:
        rows = self.connection.execute(
            "SELECT request_payload FROM human_requests WHERE status IN ('pending', 'answered') ORDER BY requested_at, request_id"
        ).fetchall()
        return [HumanRequest.from_dict(json.loads(row["request_payload"])) for row in rows]

    def save_human_response(self, response: HumanResponse) -> None:
        row = self.connection.execute(
            "SELECT status FROM human_requests WHERE request_id = ?",
            (response.request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(response.request_id)
        if row["status"] == "consumed":
            raise ValueError(f"human response already consumed: {response.request_id}")
        if row["status"] == "answered":
            raise ValueError(f"human response already recorded: {response.request_id}")
        self.connection.execute(
            "UPDATE human_requests SET status='answered', response_payload=?, responded_at=? WHERE request_id=? AND status='pending'",
            (json.dumps(response.to_dict(), ensure_ascii=False, separators=(",", ":")), response.received_at, response.request_id),
        )

    def get_human_response(self, request_id: str) -> HumanResponse | None:
        row = self.connection.execute(
            "SELECT response_payload FROM human_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None or row["response_payload"] is None:
            return None
        return HumanResponse.from_dict(json.loads(row["response_payload"]))

    def consume_human_response(self, request_id: str) -> HumanResponse:
        row = self.connection.execute(
            "SELECT status, response_payload FROM human_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(request_id)
        if row["status"] == "consumed":
            raise ValueError(f"human response already consumed: {request_id}")
        if row["status"] != "answered" or row["response_payload"] is None:
            raise ValueError(f"human response is not available: {request_id}")
        response = HumanResponse.from_dict(json.loads(row["response_payload"]))
        self.connection.execute(
            "UPDATE human_requests SET status='consumed', consumed_at=CURRENT_TIMESTAMP WHERE request_id=? AND status='answered'",
            (request_id,),
        )
        return response

    def consume_human_response_and_transition(self, request_id: str, *, task: Task, event: Event) -> HumanResponse:
        """Consume one response and publish its fresh Task continuation atomically."""
        row = self.connection.execute(
            "SELECT status, response_payload, task_id FROM human_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None:
            raise KeyError(request_id)
        if row["task_id"] != task.task_id:
            raise ValueError("human response does not belong to task")
        if row["status"] == "consumed":
            raise ValueError(f"human response already consumed: {request_id}")
        if row["status"] != "answered" or row["response_payload"] is None:
            raise ValueError(f"human response is not available: {request_id}")
        response = HumanResponse.from_dict(json.loads(row["response_payload"]))
        self.connection.execute(
            "UPDATE human_requests SET status='consumed', consumed_at=CURRENT_TIMESTAMP WHERE request_id=? AND status='answered'",
            (request_id,),
        )
        self.save_task(task)
        self.append_event(event)
        return response

    def get_idempotent(self, key: str) -> ToolResult | None:
        row = self.connection.execute("SELECT result_payload FROM idempotency WHERE idempotency_key = ?", (key,)).fetchone()
        return ToolResult.from_dict(json.loads(row["result_payload"])) if row else None

    def save_idempotent(self, key: str, result: ToolResult) -> None:
        self.connection.execute("INSERT OR IGNORE INTO idempotency VALUES (?, ?)", (key, json.dumps(result.to_dict(), ensure_ascii=False)))

    def insert_transition(
        self,
        *,
        task: Task | None = None,
        step: Step | None = None,
        checkpoint: dict[str, Any] | None = None,
        events: list[Event] | None = None,
        tool_result: ToolResult | None = None,
        human_request: HumanRequest | None = None,
    ) -> None:
        """Insert all core records for an already-open transaction."""
        if task is not None:
            self.save_task(task)
        if step is not None:
            self.save_step(step)
        if checkpoint is not None:
            self.checkpoint(
                task_id=checkpoint["task_id"],
                step_id=checkpoint["step_id"],
                phase=checkpoint["phase"],
                state=checkpoint.get("state", {}),
            )
        if tool_result is not None:
            self.save_tool_result(tool_result)
        if human_request is not None:
            self.save_human_request(human_request)
        for event in events or []:
            self.append_event(event)

    def snapshot(self) -> dict[str, Any]:
        tasks = {item["task_id"]: item for item in self._rows("tasks")}
        steps = {item["step_id"]: item for item in self._rows("steps")}
        results = {item["call_id"]: item for item in self._rows("tool_results")}
        events = self._rows("events")
        checkpoints = [dict(row) | {"state": json.loads(row["state_payload"])} for row in self.connection.execute("SELECT task_id, step_id, phase, state_payload FROM checkpoints ORDER BY sequence").fetchall()]
        return {"tasks": tasks, "steps": steps, "tool_results": results, "events": events, "checkpoints": checkpoints}

    def has_event(self, task_id: str, event_type: str) -> bool:
        rows = self.connection.execute("SELECT payload FROM events WHERE payload LIKE ?", (f'%"task_id": "{task_id}"%',)).fetchall()
        return any(json.loads(row["payload"]).get("event_type") == event_type for row in rows)

    def _rows(self, table: str, column: str = "payload") -> list[dict[str, Any]]:
        return [json.loads(row[column]) for row in self.connection.execute(f"SELECT {column} FROM {table}").fetchall()]


__all__ = ["CoreStateRepository"]
