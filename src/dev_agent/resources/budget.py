"""Fail-closed, integer-minor-unit budget reservations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .ledger import BudgetPeriod, MoneyAmount, ResourceLedger


class BudgetExceeded(RuntimeError):
    pass


class UnknownPrice(BudgetExceeded):
    pass


class ResourceUnavailable(BudgetExceeded):
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
    native_units: int | float = 1

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

    def reserve(self, task_id: str, resource_id: str, *, estimated_cost: MoneyAmount | None = None, estimated_cost_minor: int | None = None, recovery: bool = False, native_units: int | float = 1) -> BudgetReservation:
        if not task_id.strip():
            raise ValueError("task_id is required")
        if estimated_cost is not None and estimated_cost_minor is not None:
            raise ValueError("provide estimated_cost, not both money and minor units")
        if estimated_cost is None and estimated_cost_minor is not None:
            estimated_cost = MoneyAmount(self.currency, estimated_cost_minor)
        if estimated_cost is None:
            raise UnknownPrice(f"price is unknown for resource {resource_id}")
        if isinstance(native_units, bool) or not isinstance(native_units, (int, float)) or native_units <= 0:
            raise ValueError("native_units must be positive")
        if estimated_cost.currency != self.currency:
            raise BudgetExceeded(f"currency mismatch: budget={self.currency}, request={estimated_cost.currency}")
        resource = self.ledger.get_resource(resource_id)
        if resource["cost_minor"] is not None and resource["price_currency"] != self.currency:
            raise BudgetExceeded(f"currency mismatch: budget={self.currency}, resource={resource['price_currency']}")
        if resource["health"] == "unhealthy":
            raise BudgetExceeded(f"resource is unhealthy: {resource_id}")
        try:
            reservation_id = self.ledger.reserve_budget(task_id=task_id, resource_id=resource_id, amount=estimated_cost, recovery=recovery, period=self.period, normal_limit_minor=self.policy.hard_cap_minor - self.policy.recovery_reserve_minor, recovery_limit_minor=self.policy.recovery_reserve_minor, native_units=native_units)
        except ValueError as exc:
            message = str(exc)
            if message.startswith("resource capacity exceeded"):
                raise ResourceUnavailable(message) from exc
            raise BudgetExceeded(message) from exc
        return BudgetReservation(reservation_id, task_id, resource_id, estimated_cost, recovery, native_units)

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
        try:
            self.ledger.reconcile_budget(reservation_id, actual=actual_cost, period=self.period, normal_limit_minor=self.policy.hard_cap_minor - self.policy.recovery_reserve_minor, recovery_limit_minor=self.policy.recovery_reserve_minor)
        except ValueError as exc:
            raise BudgetExceeded(str(exc)) from exc
        return self.ledger.reservation_row(reservation_id)

    def release(self, reservation_id: str) -> None:
        self.ledger.release_budget(reservation_id)

    def mark_unknown(self, reservation_id: str) -> None:
        """Hold an uncertain charge until an operator/provider reconciles it."""
        self.ledger.mark_budget_unknown(reservation_id)

    def snapshot(self) -> dict[str, Any]:
        totals = self.ledger.reservation_totals(period_id=self.period.period_id)
        return {**totals, "hard_cap_minor": self.policy.hard_cap_minor, "recovery_reserve_minor": self.policy.recovery_reserve_minor, "normal_available_minor": self.policy.hard_cap_minor - self.policy.recovery_reserve_minor - totals["normal_committed_minor"], "recovery_available_minor": self.policy.recovery_reserve_minor - totals["recovery_committed_minor"], "currency": self.currency, "period_id": self.period.period_id}
