"""Small atomic JSON store used by alpha0 before the SQLite store in Phase 3."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..domain.protocol import Event, Step, Task, ToolResult


class JsonStateStore:
    """Persist the minimum task trace in one replaceable JSON document."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = {
            "schema_version": 2,
            "tasks": {},
            "steps": {},
            "tool_results": {},
            "events": [],
            "checkpoints": [],
            "approvals": {},
            "effect_intents": {},
            "approval_consumptions": {},
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
        version = value.get("schema_version", 1)
        if not isinstance(version, int) or version > 2:
            raise ValueError(f"unsupported state schema version: {version}")
        self._data["schema_version"] = 2
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

    def save_approval(self, approval_id: str, *, task_id: str, side_effect_level: str, actor: str, call_id: str, arguments_hash: str, expires_at: float | None = None) -> None:
        if approval_id in self._data.setdefault("approvals", {}):
            raise ValueError(f"approval already exists: {approval_id}")
        self._data["approvals"][approval_id] = {"task_id": task_id, "side_effect_level": side_effect_level, "actor": actor, "call_id": call_id, "arguments_hash": arguments_hash, "expires_at": expires_at, "revoked": False}
        self._flush()

    def has_approval(self, approval_id: str, *, task_id: str, side_effect_level: str, call_id: str, arguments_hash: str) -> bool:
        item = self._data.get("approvals", {}).get(approval_id)
        return bool(item and not item.get("revoked", False) and (item.get("expires_at") is None or float(item["expires_at"]) > time.time()) and item.get("task_id") == task_id and item.get("side_effect_level") == side_effect_level and item.get("call_id") == call_id and item.get("arguments_hash") == arguments_hash)

    def revoke_approval(self, approval_id: str) -> None:
        item = self._data.get("approvals", {}).get(approval_id)
        if item is None:
            raise KeyError(approval_id)
        item["revoked"] = True
        self._flush()

    def consume_approval(self, approval_id: str, *, task_id: str, side_effect_level: str, call_id: str, arguments_hash: str) -> bool:
        if not self.has_approval(approval_id, task_id=task_id, side_effect_level=side_effect_level, call_id=call_id, arguments_hash=arguments_hash):
            return False
        consumed = self._data.setdefault("approval_consumptions", {})
        if approval_id in consumed:
            return False
        consumed[approval_id] = True
        self._flush()
        return True

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

    def mark_effect_unknown(self, key: str, *, reason: str) -> None:
        intent = self._data.setdefault("effect_intents", {}).get(key)
        if intent is None:
            raise ValueError(f"effect intent not found: {key}")
        intent["status"] = "unknown"
        intent["result"] = {"unknown": True, "reason": reason}
        self._flush()
