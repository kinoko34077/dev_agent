"""Fail-closed, integer-minor-unit budget reservations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .ledger import BudgetPeriod, MoneyAmount, ResourceLedger


class BudgetExceeded(RuntimeError):
    pass


class UnknownPrice(BudgetExceeded):
    pass


@dataclass(frozen=True)
class BudgetPolicy:
    hard_cap_minor: int
    recovery_reserve_minor: int
    currency: str = "JPY"
    period: BudgetPeriod | None = None


@dataclass(frozen=True)
class BudgetReservation:
    reservation_id: str
    task_id: str
    resource_id: str
    estimated_cost: MoneyAmount
    recovery: bool

    @property
    def estimated_cost_minor(self) -> int:
        """Compatibility accessor; new callers must pass ``MoneyAmount``."""
        return self.estimated_cost.minor_units


class BudgetGovernor:
    def __init__(self, ledger: ResourceLedger, policy: BudgetPolicy) -> None:
        self.ledger = ledger
        if policy.hard_cap_minor < 0 or policy.recovery_reserve_minor < 0 or policy.recovery_reserve_minor > policy.hard_cap_minor:
            raise ValueError("invalid budget policy")
        self.policy = policy
        self.period = policy.period or self._current_month()
        self.currency = policy.currency.upper()
        self.ledger.configure_budget(hard_cap_minor=policy.hard_cap_minor, recovery_reserve_minor=policy.recovery_reserve_minor, currency=self.currency, period=self.period)

    @staticmethod
    def _current_month() -> BudgetPeriod:
        now = datetime.now(timezone.utc)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
        return BudgetPeriod(start.strftime("%Y-%m"), start.isoformat(), end.isoformat())

    def reserve(self, task_id: str, resource_id: str, *, estimated_cost: MoneyAmount | None = None, estimated_cost_minor: int | None = None, recovery: bool = False) -> BudgetReservation:
        if not task_id.strip():
            raise ValueError("task_id is required")
        if estimated_cost is not None and estimated_cost_minor is not None:
            raise ValueError("provide estimated_cost, not both money and minor units")
        if estimated_cost is None and estimated_cost_minor is not None:
            estimated_cost = MoneyAmount(self.currency, estimated_cost_minor)
        if estimated_cost is None:
            raise UnknownPrice(f"price is unknown for resource {resource_id}")
        if estimated_cost.currency != self.currency:
            raise BudgetExceeded(f"currency mismatch: budget={self.currency}, request={estimated_cost.currency}")
        resource = self.ledger.get_resource(resource_id)
        if resource["cost_minor"] is not None and resource["price_currency"] != self.currency:
            raise BudgetExceeded(f"currency mismatch: budget={self.currency}, resource={resource['price_currency']}")
        if resource["health"] == "unhealthy":
            raise BudgetExceeded(f"resource is unhealthy: {resource_id}")
        with self.ledger._lock:
            self.ledger.connection.execute("BEGIN IMMEDIATE")
            try:
                totals = self.ledger.reservation_totals(period_id=self.period.period_id)
                if recovery:
                    remaining = self.policy.recovery_reserve_minor - totals["recovery_committed_minor"]
                else:
                    normal_cap = self.policy.hard_cap_minor - self.policy.recovery_reserve_minor
                    remaining = normal_cap - totals["normal_committed_minor"]
                if estimated_cost.minor_units > remaining:
                    self.ledger.connection.rollback()
                    raise BudgetExceeded(f"budget exceeded for {resource_id}: requested {estimated_cost.minor_units}, remaining {remaining}")
                reservation_id = str(uuid4())
                self.ledger.connection.execute("INSERT INTO budget_reservations(reservation_id, task_id, resource_id, estimated_minor, actual_minor, recovery, status, created_at, reconciled_at, period_id, currency) VALUES (?, ?, ?, ?, NULL, ?, 'reserved', ?, NULL, ?, ?)", (reservation_id, task_id, resource_id, estimated_cost.minor_units, int(recovery), datetime.now(timezone.utc).isoformat(), self.period.period_id, self.currency))
                self.ledger.connection.commit()
            except Exception:
                if self.ledger.connection.in_transaction:
                    self.ledger.connection.rollback()
                raise
        return BudgetReservation(reservation_id, task_id, resource_id, estimated_cost, recovery)

    def reconcile(self, reservation_id: str, *, actual_cost: MoneyAmount | None = None, actual_cost_minor: int | None = None) -> dict[str, Any]:
        if actual_cost is not None and actual_cost_minor is not None:
            raise ValueError("provide actual_cost, not both money and minor units")
        if actual_cost is None and actual_cost_minor is not None:
            actual_cost = MoneyAmount(self.currency, actual_cost_minor)
        if actual_cost is None:
            self.mark_unknown(reservation_id)
            return self.ledger.reservation_row(reservation_id)
        if actual_cost.currency != self.currency:
            raise BudgetExceeded(f"currency mismatch: budget={self.currency}, actual={actual_cost.currency}")
        with self.ledger._lock:
            self.ledger.connection.execute("BEGIN IMMEDIATE")
            try:
                row = self.ledger.connection.execute("SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
                if row is None:
                    raise KeyError(reservation_id)
                if row["status"] not in {"reserved", "unknown"}:
                    raise ValueError(f"reservation is not active: {reservation_id}")
                if row["period_id"] != self.period.period_id or row["currency"] != self.currency:
                    raise BudgetExceeded("reservation does not belong to current budget period or currency")
                totals = self.ledger.reservation_totals(period_id=self.period.period_id)
                current_total = totals["recovery_committed_minor"] if row["recovery"] else totals["normal_committed_minor"]
                current_total -= int(row["estimated_minor"])
                limit = self.policy.recovery_reserve_minor if row["recovery"] else self.policy.hard_cap_minor - self.policy.recovery_reserve_minor
                if current_total + actual_cost.minor_units > limit:
                    raise BudgetExceeded("actual usage exceeds the protected budget")
                self.ledger.connection.execute("UPDATE budget_reservations SET actual_minor=?, status='reconciled', reconciled_at=? WHERE reservation_id=?", (actual_cost.minor_units, datetime.now(timezone.utc).isoformat(), reservation_id))
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
        totals = self.ledger.reservation_totals(period_id=self.period.period_id)
        return {**totals, "hard_cap_minor": self.policy.hard_cap_minor, "recovery_reserve_minor": self.policy.recovery_reserve_minor, "normal_available_minor": self.policy.hard_cap_minor - self.policy.recovery_reserve_minor - totals["normal_committed_minor"], "recovery_available_minor": self.policy.recovery_reserve_minor - totals["recovery_committed_minor"], "currency": self.currency, "period_id": self.period.period_id}
