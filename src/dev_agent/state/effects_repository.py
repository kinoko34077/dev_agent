"""Effect, approval, and provider-audit SQL for the SQLite state facade."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Mapping

from ..domain.protocol import ToolResult


class EffectAuditRepository:
    """Execute effect SQL without owning locks, transactions, or lease policy."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def save_approval(
        self,
        approval_id: str,
        *,
        task_id: str,
        side_effect_level: str,
        actor: str,
        call_id: str,
        arguments_hash: str,
        expires_at: float | None,
    ) -> None:
        self.connection.execute(
            "INSERT INTO approvals(approval_id, task_id, side_effect_level, actor, call_id, arguments_hash, expires_at, revoked) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (approval_id, task_id, side_effect_level, actor, call_id, arguments_hash, expires_at),
        )

    def has_approval(
        self,
        approval_id: str,
        *,
        task_id: str,
        side_effect_level: str,
        call_id: str,
        arguments_hash: str,
        now: float,
    ) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM approvals WHERE approval_id = ? AND task_id = ? AND side_effect_level = ? AND call_id = ? AND arguments_hash = ? AND revoked = 0 AND (expires_at IS NULL OR expires_at > ?)",
            (approval_id, task_id, side_effect_level, call_id, arguments_hash, now),
        ).fetchone()
        return row is not None

    def revoke_approval(self, approval_id: str) -> int:
        cursor = self.connection.execute("UPDATE approvals SET revoked = 1 WHERE approval_id = ?", (approval_id,))
        return cursor.rowcount

    def consume_approval(
        self,
        approval_id: str,
        *,
        task_id: str,
        side_effect_level: str,
        call_id: str,
        arguments_hash: str,
        now: float,
    ) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM approvals WHERE approval_id = ? AND task_id = ? AND side_effect_level = ? AND call_id = ? AND arguments_hash = ? AND revoked = 0 AND (expires_at IS NULL OR expires_at > ?)",
            (approval_id, task_id, side_effect_level, call_id, arguments_hash, now),
        ).fetchone()
        if row is None:
            return False
        cursor = self.connection.execute("INSERT OR IGNORE INTO approval_consumptions(approval_id) VALUES (?)", (approval_id,))
        return cursor.rowcount == 1

    def get_effect_intent(self, key: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT * FROM effect_intents WHERE idempotency_key = ?", (key,)).fetchone()
        if row is None:
            return None
        return {
            "idempotency_key": row["idempotency_key"],
            "task_id": row["task_id"],
            "tool_name": row["tool_name"],
            "arguments": json.loads(row["arguments_payload"]),
            "status": row["status"],
            "result": json.loads(row["result_payload"]) if row["result_payload"] else None,
        }

    def create_effect_intent(self, key: str, *, task_id: str, tool_name: str, arguments: dict[str, Any]) -> bool:
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO effect_intents(idempotency_key, task_id, tool_name, arguments_payload, status) VALUES (?, ?, ?, ?, 'pending')",
            (key, task_id, tool_name, json.dumps(arguments, ensure_ascii=False)),
        )
        return cursor.rowcount == 1

    def claim_effect_intent(
        self,
        key: str,
        *,
        expected_statuses: set[str] | frozenset[str] | tuple[str, ...],
        result: dict[str, Any] | None,
    ) -> bool:
        """Atomically claim the right to cross an external effect boundary.

        The caller must hold the StateStore transaction lock.  A conditional
        UPDATE is the claim: exactly one connection can move a pending or
        prepared intent to ``dispatching``.  The claim deliberately does not
        add a second scheduler/ownership table; a caller that loses the CAS
        re-reads the existing intent and follows its durable outcome.
        """

        allowed = {"pending", "prepared"}
        statuses = tuple(sorted(set(expected_statuses) & allowed))
        if not statuses:
            raise ValueError("expected_statuses must contain pending or prepared")
        if result is not None:
            payload = json.dumps(result, ensure_ascii=False)
            cursor = self.connection.execute(
                f"UPDATE effect_intents SET status='dispatching', result_payload=? "
                f"WHERE idempotency_key=? AND status IN ({','.join('?' for _ in statuses)})",
                (payload, key, *statuses),
            )
        else:
            cursor = self.connection.execute(
                f"UPDATE effect_intents SET status='dispatching' "
                f"WHERE idempotency_key=? AND status IN ({','.join('?' for _ in statuses)})",
                (key, *statuses),
            )
        return cursor.rowcount == 1

    def transition_effect_intent(
        self,
        key: str,
        *,
        to_status: str,
        result: dict[str, Any] | None,
        transitions: Mapping[str, set[str]],
    ) -> None:
        allowed = {"prepared", "dispatching", "unknown", "succeeded", "confirmed_failed", "reconciling", "reconciled"}
        if to_status not in allowed:
            raise ValueError(f"invalid effect intent status: {to_status}")
        row = self.connection.execute("SELECT status FROM effect_intents WHERE idempotency_key = ?", (key,)).fetchone()
        if row is None:
            raise ValueError(f"effect intent not found: {key}")
        current = row["status"]
        if to_status != current and to_status not in transitions.get(current, set()):
            raise ValueError(f"invalid effect intent transition: {current} -> {to_status}")
        payload = json.dumps(result, ensure_ascii=False) if result is not None else None
        self.connection.execute(
            "UPDATE effect_intents SET status = ?, result_payload = COALESCE(?, result_payload) WHERE idempotency_key = ?",
            (to_status, payload, key),
        )

    def record_provider_audit(
        self,
        *,
        task_id: str,
        request_id: str,
        intent_key: str | None,
        provider_id: str,
        resource_id: str,
        native_unit: str,
        estimated_cost_minor: int | None,
        price_currency: str | None,
        outcome: str,
        details: dict[str, Any] | None,
    ) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (task_id, request_id, provider_id, resource_id, native_unit, outcome)):
            raise ValueError("provider audit identity and outcome are required")
        if estimated_cost_minor is not None and (isinstance(estimated_cost_minor, bool) or not isinstance(estimated_cost_minor, int) or estimated_cost_minor < 0):
            raise ValueError("estimated_cost_minor must be a non-negative integer or None")
        self.connection.execute(
            "INSERT INTO provider_dispatch_audits(task_id, request_id, intent_key, provider_id, resource_id, native_unit, estimated_cost_minor, price_currency, outcome, details_payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, request_id, intent_key, provider_id, resource_id, native_unit, estimated_cost_minor, price_currency, outcome, json.dumps(details or {}, ensure_ascii=False)),
        )

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
        rows = self.connection.execute(
            f"SELECT sequence, task_id, request_id, intent_key, provider_id, resource_id, native_unit, estimated_cost_minor, price_currency, outcome, details_payload, recorded_at FROM provider_dispatch_audits{where} ORDER BY sequence",
            values,
        ).fetchall()
        return [dict(row) | {"details": json.loads(row["details_payload"])} for row in rows]

    def reconcile_effect_intent(
        self,
        key: str,
        *,
        status: str,
        actor: str,
        source: str,
        external_id: str | None,
        evidence: dict[str, Any] | None,
        result: ToolResult | None,
        transitions: Mapping[str, set[str]],
    ) -> None:
        if status not in {"succeeded", "confirmed_failed", "unknown"}:
            raise ValueError(f"invalid reconciliation status: {status}")
        if not actor.strip() or not source.strip():
            raise ValueError("reconciliation actor and source are required")
        row = self.connection.execute("SELECT status FROM effect_intents WHERE idempotency_key = ?", (key,)).fetchone()
        if row is None:
            raise ValueError(f"effect intent not found: {key}")
        current = row["status"]
        if current != "reconciling" and "reconciling" not in transitions.get(current, set()):
            raise ValueError(f"invalid effect intent transition: {current} -> reconciling")
        if status not in transitions["reconciling"] and status != current:
            raise ValueError(f"invalid effect intent transition: reconciling -> {status}")
        audit = {"actor": actor, "source": source, "external_id": external_id, "evidence": evidence or {}}
        payload = result.to_dict() if result is not None else audit
        self.connection.execute(
            "INSERT INTO effect_reconciliations(idempotency_key, status, actor, source, external_id, evidence_payload) VALUES (?, ?, ?, ?, ?, ?)",
            (key, status, actor, source, external_id, json.dumps(evidence or {}, ensure_ascii=False)),
        )
        self.connection.execute("UPDATE effect_intents SET status = 'reconciling' WHERE idempotency_key = ?", (key,))
        self.connection.execute(
            "UPDATE effect_intents SET status = ?, result_payload = ? WHERE idempotency_key = ?",
            (status, json.dumps(payload, ensure_ascii=False), key),
        )


__all__ = ["EffectAuditRepository"]
