"""Small atomic JSON store used by alpha0 before the SQLite store in Phase 3."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain.protocol import Event, Step, Task, ToolResult


class JsonStateStore:
    """Persist the minimum task trace in one replaceable JSON document."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {
            "tasks": {},
            "steps": {},
            "tool_results": {},
            "events": [],
            "checkpoints": [],
            "approvals": {},
            "effect_intents": {},
        }
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"state store cannot be read: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("state store root must be an object")
        for key in self._data:
            if key in value:
                self._data[key] = value[key]

    def _flush(self) -> None:
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._data, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def save_task(self, task: Task) -> None:
        self._data["tasks"][task.task_id] = task.to_dict()
        self._flush()

    def save_step(self, step: Step) -> None:
        self._data["steps"][step.step_id] = step.to_dict()
        self._flush()

    def save_tool_result(self, result: ToolResult) -> None:
        self._data["tool_results"][result.call_id] = result.to_dict()
        self._flush()

    def append_event(self, event: Event) -> None:
        self._data["events"].append(event.to_dict())
        self._flush()

    def checkpoint(self, *, task_id: str, step_id: str, phase: str, state: dict[str, Any]) -> None:
        self._data["checkpoints"].append({"task_id": task_id, "step_id": step_id, "phase": phase, "state": state})
        self._flush()

    def load_latest_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        for checkpoint in reversed(self._data["checkpoints"]):
            if checkpoint["task_id"] == task_id:
                return dict(checkpoint)
        return None

    def snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self._data))

    def load_task(self, task_id: str) -> Task | None:
        value = self._data["tasks"].get(task_id)
        return Task.from_dict(value) if value else None

    def get_idempotent(self, key: str) -> ToolResult | None:
        value = self._data.setdefault("idempotency", {}).get(key)
        return ToolResult.from_dict(value) if value else None

    def save_idempotent(self, key: str, result: ToolResult) -> None:
        self._data.setdefault("idempotency", {})[key] = result.to_dict()
        self._flush()

    def save_approval(self, approval_id: str, *, task_id: str, side_effect_level: str, actor: str) -> None:
        self._data.setdefault("approvals", {})[approval_id] = {"task_id": task_id, "side_effect_level": side_effect_level, "actor": actor}
        self._flush()

    def has_approval(self, approval_id: str, *, task_id: str, side_effect_level: str) -> bool:
        item = self._data.get("approvals", {}).get(approval_id)
        return bool(item and item.get("task_id") == task_id and item.get("side_effect_level") == side_effect_level)

    def get_effect_intent(self, key: str) -> dict[str, Any] | None:
        return self._data.get("effect_intents", {}).get(key)

    def create_effect_intent(self, key: str, *, task_id: str, tool_name: str, arguments: dict[str, Any]) -> bool:
        intents = self._data.setdefault("effect_intents", {})
        if key in intents:
            return False
        intents[key] = {"task_id": task_id, "tool_name": tool_name, "arguments": arguments, "status": "pending"}
        self._flush()
        return True

    def complete_effect_intent(self, key: str, result: ToolResult) -> None:
        intent = self._data.setdefault("effect_intents", {}).get(key)
        if intent is None:
            raise ValueError(f"effect intent not found: {key}")
        intent["status"] = "succeeded"
        intent["result"] = result.to_dict()
        self._flush()
