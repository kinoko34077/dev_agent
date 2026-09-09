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
from .core_repository import CoreStateRepository
from .effects_repository import EffectAuditRepository
from .schema import StateSchema


def _serialized(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapped


class SQLiteStateStore:
    SCHEMA_VERSION = StateSchema.VERSION
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

    # Keep the historical names available to migration tests and callers;
    # schema ownership lives in the dedicated StateSchema module.
    _V1_SCHEMA = StateSchema.V1_SCHEMA
    _LATEST_SCHEMA = StateSchema.LATEST_SCHEMA

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
        self._core = CoreStateRepository(self.connection)
        self._effects = EffectAuditRepository(self.connection)
        StateSchema.initialize(self.connection)

    @_serialized
    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "SQLiteStateStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @_serialized
    def save_task(self, task: Task) -> None:
        self._core.save_task(task)
        self.connection.commit()

    @_serialized
    def save_step(self, step: Step) -> None:
        self._core.save_step(step)
        self.connection.commit()

    @_serialized
    def save_tool_result(self, result: ToolResult) -> None:
        self._core.save_tool_result(result)
        self.connection.commit()

    @_serialized
    def append_event(self, event: Event) -> None:
        self._core.append_event(event)
        self.connection.commit()

    @_serialized
    def checkpoint(self, *, task_id: str, step_id: str, phase: str, state: dict[str, Any]) -> None:
        self._core.checkpoint(task_id=task_id, step_id=step_id, phase=phase, state=state)
        self.connection.commit()

    @_serialized
    def load_latest_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        return self._core.load_latest_checkpoint(task_id)

    @_serialized
    def load_task(self, task_id: str) -> Task | None:
        return self._core.load_task(task_id)

    @_serialized
    def get_idempotent(self, key: str) -> ToolResult | None:
        return self._core.get_idempotent(key)

    @_serialized
    def save_idempotent(self, key: str, result: ToolResult) -> None:
        self._core.save_idempotent(key, result)
        self.connection.commit()

    @_serialized
    def save_approval(self, approval_id: str, *, task_id: str, side_effect_level: str, actor: str, call_id: str, arguments_hash: str, expires_at: float | None = None) -> None:
        try:
            self._effects.save_approval(
                approval_id,
                task_id=task_id,
                side_effect_level=side_effect_level,
                actor=actor,
                call_id=call_id,
                arguments_hash=arguments_hash,
                expires_at=expires_at,
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    @_serialized
    def has_approval(self, approval_id: str, *, task_id: str, side_effect_level: str, call_id: str, arguments_hash: str) -> bool:
        return self._effects.has_approval(
            approval_id,
            task_id=task_id,
            side_effect_level=side_effect_level,
            call_id=call_id,
            arguments_hash=arguments_hash,
            now=time.time(),
        )

    @_serialized
    def revoke_approval(self, approval_id: str) -> None:
        row_count = self._effects.revoke_approval(approval_id)
        self.connection.commit()
        if row_count != 1:
            raise KeyError(approval_id)

    @_serialized
    def consume_approval(self, approval_id: str, *, task_id: str, side_effect_level: str, call_id: str, arguments_hash: str) -> bool:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            consumed = self._effects.consume_approval(
                approval_id,
                task_id=task_id,
                side_effect_level=side_effect_level,
                call_id=call_id,
                arguments_hash=arguments_hash,
                now=time.time(),
            )
            self.connection.commit()
            return consumed
        except Exception:
            self.connection.rollback()
            raise

    @_serialized
    def get_effect_intent(self, key: str) -> dict[str, Any] | None:
        return self._effects.get_effect_intent(key)

    @_serialized
    def create_effect_intent(self, key: str, *, task_id: str, tool_name: str, arguments: dict[str, Any]) -> bool:
        created = self._effects.create_effect_intent(key, task_id=task_id, tool_name=tool_name, arguments=arguments)
        self.connection.commit()
        return created

    @_serialized
    def complete_effect_intent(self, key: str, result: ToolResult) -> None:
        self.transition_effect_intent(key, to_status="succeeded", result=result.to_dict())

    @_serialized
    def mark_effect_unknown(self, key: str, *, reason: str) -> None:
        self.transition_effect_intent(key, to_status="unknown", result={"unknown": True, "reason": reason})

    @_serialized
    def transition_effect_intent(self, key: str, *, to_status: str, result: dict[str, Any] | None = None, lease_proof: Any | None = None) -> None:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            if lease_proof is not None and self.connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='queue_items'").fetchone() is not None:
                row = self.connection.execute("SELECT 1 FROM queue_items WHERE task_id=? AND state='leased' AND lease_owner=? AND lease_token=? AND state_version=? AND lease_until > ?", (lease_proof.task_id, lease_proof.worker_id, lease_proof.lease_token, lease_proof.state_version, time.time())).fetchone()
                if row is None:
                    from ..scheduler.queue import StaleLease
                    raise StaleLease(lease_proof.task_id)
            self._effects.transition_effect_intent(
                key,
                to_status=to_status,
                result=result,
                transitions=self._EFFECT_TRANSITIONS,
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    @_serialized
    def record_provider_audit(self, *, task_id: str, request_id: str, intent_key: str | None, provider_id: str, resource_id: str, native_unit: str, estimated_cost_minor: int | None, price_currency: str | None, outcome: str, details: dict[str, Any] | None = None) -> None:
        self._effects.record_provider_audit(
            task_id=task_id,
            request_id=request_id,
            intent_key=intent_key,
            provider_id=provider_id,
            resource_id=resource_id,
            native_unit=native_unit,
            estimated_cost_minor=estimated_cost_minor,
            price_currency=price_currency,
            outcome=outcome,
            details=details,
        )
        self.connection.commit()

    @_serialized
    def list_provider_audits(self, *, task_id: str | None = None, request_id: str | None = None) -> list[dict[str, Any]]:
        return self._effects.list_provider_audits(task_id=task_id, request_id=request_id)

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
            transition_events = list(events or [])
            if event is not None:
                transition_events.append(event)
            self._core.insert_transition(
                task=task,
                step=step,
                checkpoint=checkpoint,
                events=transition_events,
                tool_result=tool_result,
            )
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
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self._effects.reconcile_effect_intent(
                key,
                status=status,
                actor=actor,
                source=source,
                external_id=external_id,
                evidence=evidence,
                result=result,
                transitions=self._EFFECT_TRANSITIONS,
            )
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise

    @_serialized
    def snapshot(self) -> dict[str, Any]:
        return self._core.snapshot()

    @_serialized
    def has_event(self, task_id: str, event_type: str) -> bool:
        return self._core.has_event(task_id, event_type)
