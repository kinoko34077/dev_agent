"""Fail-closed, integer-minor-unit budget reservations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .ledger import ResourceLedger


class BudgetExceeded(RuntimeError):
    pass


class UnknownPrice(BudgetExceeded):
    pass


@dataclass(frozen=True)
class BudgetPolicy:
    hard_cap_minor: int
    recovery_reserve_minor: int
    currency: str = "JPY"


@dataclass(frozen=True)
class BudgetReservation:
    reservation_id: str
    task_id: str
    resource_id: str
    estimated_cost_minor: int
    recovery: bool


class BudgetGovernor:
    def __init__(self, ledger: ResourceLedger, policy: BudgetPolicy) -> None:
        self.ledger = ledger
        if policy.hard_cap_minor < 0 or policy.recovery_reserve_minor < 0 or policy.recovery_reserve_minor > policy.hard_cap_minor:
            raise ValueError("invalid budget policy")
        self.policy = policy
        self.ledger.configure_budget(hard_cap_minor=policy.hard_cap_minor, recovery_reserve_minor=policy.recovery_reserve_minor, currency=policy.currency)

    def reserve(self, task_id: str, resource_id: str, *, estimated_cost_minor: int | None, recovery: bool = False) -> BudgetReservation:
        if not task_id.strip():
            raise ValueError("task_id is required")
        if estimated_cost_minor is None:
            raise UnknownPrice(f"price is unknown for resource {resource_id}")
        if isinstance(estimated_cost_minor, bool) or not isinstance(estimated_cost_minor, int) or estimated_cost_minor < 0:
            raise ValueError("estimated_cost_minor must be a non-negative integer")
        resource = self.ledger.get_resource(resource_id)
        if resource["health"] == "unhealthy":
            raise BudgetExceeded(f"resource is unhealthy: {resource_id}")
        with self.ledger._lock:
            self.ledger.connection.execute("BEGIN IMMEDIATE")
            try:
                totals = self.ledger.reservation_totals()
                if recovery:
                    remaining = self.policy.recovery_reserve_minor - totals["recovery_committed_minor"]
                else:
                    normal_cap = self.policy.hard_cap_minor - self.policy.recovery_reserve_minor
                    remaining = normal_cap - totals["normal_committed_minor"]
                if estimated_cost_minor > remaining:
                    self.ledger.connection.rollback()
                    raise BudgetExceeded(f"budget exceeded for {resource_id}: requested {estimated_cost_minor}, remaining {remaining}")
                reservation_id = str(uuid4())
                self.ledger.connection.execute("INSERT INTO budget_reservations VALUES (?, ?, ?, ?, NULL, ?, 'reserved', ?, NULL)", (reservation_id, task_id, resource_id, estimated_cost_minor, int(recovery), datetime.now(timezone.utc).isoformat()))
                self.ledger.connection.commit()
            except Exception:
                if self.ledger.connection.in_transaction:
                    self.ledger.connection.rollback()
                raise
        return BudgetReservation(reservation_id, task_id, resource_id, estimated_cost_minor, recovery)

    def reconcile(self, reservation_id: str, *, actual_cost_minor: int) -> dict[str, Any]:
        if isinstance(actual_cost_minor, bool) or not isinstance(actual_cost_minor, int) or actual_cost_minor < 0:
            raise ValueError("actual_cost_minor must be a non-negative integer")
        with self.ledger._lock:
            self.ledger.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.ledger.connection.execute("SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
                if row is None:
                    raise KeyError(reservation_id)
                if row["status"] not in {"reserved", "unknown"}:
                    raise ValueError(f"reservation is not active: {reservation_id}")
                totals = self.ledger.reservation_totals()
                current_total = totals["recovery_committed_minor"] if row["recovery"] else totals["normal_committed_minor"]
                current_total -= int(row["estimated_minor"])
                limit = self.policy.recovery_reserve_minor if row["recovery"] else self.policy.hard_cap_minor - self.policy.recovery_reserve_minor
                if current_total + actual_cost_minor > limit:
                    raise BudgetExceeded("actual usage exceeds the protected budget")
                self.ledger.connection.execute("UPDATE budget_reservations SET actual_minor=?, status='reconciled', reconciled_at=? WHERE reservation_id=?", (actual_cost_minor, datetime.now(timezone.utc).isoformat(), reservation_id))
                self.ledger.connection.commit()
            except Exception:
                self.ledger.connection.rollback()
                raise
        return self.ledger.reservation_row(reservation_id)

    def release(self, reservation_id: str) -> None:
        with self.ledger._lock:
            cursor = self.ledger.connection.execute("UPDATE budget_reservations SET status='released', reconciled_at=? WHERE reservation_id=? AND status='reserved'", (datetime.now(timezone.utc).isoformat(), reservation_id))
            self.ledger.connection.commit()
            if cursor.rowcount != 1:
                raise ValueError(f"reservation is not active: {reservation_id}")

    def mark_unknown(self, reservation_id: str) -> None:
        """Hold an uncertain charge until an operator/provider reconciles it."""
        with self.ledger._lock:
            cursor = self.ledger.connection.execute("UPDATE budget_reservations SET status='unknown' WHERE reservation_id=? AND status='reserved'", (reservation_id,))
            self.ledger.connection.commit()
            if cursor.rowcount != 1:
                raise ValueError(f"reservation is not active: {reservation_id}")

    def snapshot(self) -> dict[str, Any]:
        totals = self.ledger.reservation_totals()
        return {**totals, "hard_cap_minor": self.policy.hard_cap_minor, "recovery_reserve_minor": self.policy.recovery_reserve_minor, "normal_available_minor": self.policy.hard_cap_minor - self.policy.recovery_reserve_minor - totals["normal_committed_minor"], "recovery_available_minor": self.policy.recovery_reserve_minor - totals["recovery_committed_minor"], "currency": self.policy.currency}
