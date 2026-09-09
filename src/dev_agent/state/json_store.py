"""Small atomic JSON store used by alpha0 before the SQLite store in Phase 3."""

from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..domain.protocol import Event, Step, Task, ToolResult


class JsonStateStore:
    """Persist the minimum task trace in one replaceable JSON document."""

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
            "effect_reconciliations": [],
            "provider_dispatch_audits": [],
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

    def has_event(self, task_id: str, event_type: str) -> bool:
        return any(item.get("task_id") == task_id and item.get("event_type") == event_type for item in self._data.get("events", []))

    def load_task(self, task_id: str) -> Task | None:
        value = self._data["tasks"].get(task_id)
        return Task.from_persisted_dict(value) if value else None

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
        self.transition_effect_intent(key, to_status="succeeded", result=result.to_dict())

    def mark_effect_unknown(self, key: str, *, reason: str) -> None:
        self.transition_effect_intent(key, to_status="unknown", result={"unknown": True, "reason": reason})

    def transition_effect_intent(self, key: str, *, to_status: str, result: dict[str, Any] | None = None, lease_proof: Any | None = None) -> None:
        allowed = {"prepared", "dispatching", "unknown", "succeeded", "confirmed_failed", "reconciling", "reconciled"}
        if to_status not in allowed:
            raise ValueError(f"invalid effect intent status: {to_status}")
        intent = self._data.setdefault("effect_intents", {}).get(key)
        if intent is None:
            raise ValueError(f"effect intent not found: {key}")
        current = intent.get("status", "pending")
        if to_status != current and to_status not in self._EFFECT_TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid effect intent transition: {current} -> {to_status}")
        before = deepcopy(self._data)
        try:
            intent["status"] = to_status
            if result is not None:
                intent["result"] = result
            self._flush()
        except BaseException:
            self._data = before
            raise

    def record_provider_audit(self, *, task_id: str, request_id: str, intent_key: str | None, provider_id: str, resource_id: str, native_unit: str, estimated_cost_minor: int | None, price_currency: str | None, outcome: str, details: dict[str, Any] | None = None) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (task_id, request_id, provider_id, resource_id, native_unit, outcome)):
            raise ValueError("provider audit identity and outcome are required")
        if estimated_cost_minor is not None and (isinstance(estimated_cost_minor, bool) or not isinstance(estimated_cost_minor, int) or estimated_cost_minor < 0):
            raise ValueError("estimated_cost_minor must be a non-negative integer or None")
        self._data.setdefault("provider_dispatch_audits", []).append({
            "task_id": task_id,
            "request_id": request_id,
            "intent_key": intent_key,
            "provider_id": provider_id,
            "resource_id": resource_id,
            "native_unit": native_unit,
            "estimated_cost_minor": estimated_cost_minor,
            "price_currency": price_currency,
            "outcome": outcome,
            "details": details or {},
        })
        self._flush()

    def list_provider_audits(self, *, task_id: str | None = None, request_id: str | None = None) -> list[dict[str, Any]]:
        audits = self._data.get("provider_dispatch_audits", [])
        return [item for item in audits if (task_id is None or item.get("task_id") == task_id) and (request_id is None or item.get("request_id") == request_id)]

    def commit_transition(self, *, task: Task | None = None, step: Step | None = None, checkpoint: dict[str, Any] | None = None, event: Event | None = None, events: list[Event] | None = None, tool_result: ToolResult | None = None, lease_proof: Any | None = None) -> None:
        before = deepcopy(self._data)
        try:
            if task is not None:
                self._data["tasks"][task.task_id] = task.to_dict()
            if step is not None:
                self._data["steps"][step.step_id] = step.to_dict()
            if checkpoint is not None:
                self._data["checkpoints"].append(checkpoint)
            if tool_result is not None:
                self._data.setdefault("tool_results", {})[tool_result.call_id] = tool_result.to_dict()
            for transition_event in [*(events or []), *([event] if event is not None else [])]:
                self._data.setdefault("events", []).append(transition_event.to_dict())
            self._flush()
        except BaseException:
            self._data = before
            raise

    def reconcile_effect_intent(self, key: str, *, status: str, actor: str, source: str, external_id: str | None = None, evidence: dict[str, Any] | None = None, result: ToolResult | None = None) -> None:
        if status not in {"succeeded", "confirmed_failed", "unknown"}:
            raise ValueError(f"invalid reconciliation status: {status}")
        if not actor.strip() or not source.strip():
            raise ValueError("reconciliation actor and source are required")
        if self.get_effect_intent(key) is None:
            raise ValueError(f"effect intent not found: {key}")
        intent = self._data.setdefault("effect_intents", {}).get(key)
        if intent is None:
            raise ValueError(f"effect intent not found: {key}")
        current = intent.get("status", "pending")
        if current != "reconciling" and "reconciling" not in self._EFFECT_TRANSITIONS.get(current, set()):
            raise ValueError(f"invalid effect intent transition: {current} -> reconciling")
        if status != "reconciling" and status not in self._EFFECT_TRANSITIONS["reconciling"] and status != current:
            raise ValueError(f"invalid effect intent transition: reconciling -> {status}")
        audit = {"idempotency_key": key, "status": status, "actor": actor, "source": source, "external_id": external_id, "evidence": evidence or {}}
        before = deepcopy(self._data)
        try:
            intent["status"] = "reconciling"
            intent["status"] = status
            intent["result"] = result.to_dict() if result is not None else {"actor": actor, "source": source, "external_id": external_id, "evidence": evidence or {}}
            self._data.setdefault("effect_reconciliations", []).append(audit)
            self._flush()
        except BaseException:
            self._data = before
            raise
