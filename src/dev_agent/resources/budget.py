"""Fail-closed, integer-minor-unit budget reservations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from ..domain.protocol import Task, TaskClass
from .ledger import _BUDGET_ADMIN_TOKEN, BudgetPeriod, MoneyAmount, ResourceLedger


class BudgetExceeded(RuntimeError):
    pass


class BudgetReconciliationRequired(BudgetExceeded):
    """A prior dispatch left a charge-bearing reservation unresolved."""


class UnknownPrice(BudgetExceeded):
    pass


class ResourceUnavailable(BudgetExceeded):
    pass


class MaintenanceActive(BudgetExceeded):
    pass


@dataclass(frozen=True)
class BudgetPolicy:
    hard_cap_minor: int
    recovery_reserve_minor: int
    currency: str = "JPY"
    period: BudgetPeriod | None = None


_RECOVERY_RESERVE_TOKEN = object()


def _current_month() -> BudgetPeriod:
    now = datetime.now(timezone.utc)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return BudgetPeriod(start.strftime("%Y-%m"), start.isoformat(), end.isoformat())


def _validate_policy(policy: BudgetPolicy) -> None:
    if isinstance(policy.hard_cap_minor, bool) or not isinstance(policy.hard_cap_minor, int) or policy.hard_cap_minor < 0:
        raise ValueError("invalid budget policy")
    if isinstance(policy.recovery_reserve_minor, bool) or not isinstance(policy.recovery_reserve_minor, int) or policy.recovery_reserve_minor < 0 or policy.recovery_reserve_minor > policy.hard_cap_minor:
        raise ValueError("invalid budget policy")
    MoneyAmount(policy.currency, 0)
    if policy.period is not None and not isinstance(policy.period, BudgetPeriod):
        raise ValueError("invalid budget period")


class BudgetAuthority:
    """Explicit administrative boundary for changing persisted budget policy."""

    @staticmethod
    def configure(ledger: ResourceLedger, policy: BudgetPolicy) -> None:
        _validate_policy(policy)
        period = policy.period or _current_month()
        ledger.configure_budget(
            hard_cap_minor=policy.hard_cap_minor,
            recovery_reserve_minor=policy.recovery_reserve_minor,
            currency=MoneyAmount(policy.currency, 0).currency,
            period=period,
            _authority=_BUDGET_ADMIN_TOKEN,
        )

    @staticmethod
    def configure_from_protected_file(
        ledger: ResourceLedger,
        config_path: str | Path,
        *,
        agent_root: str | Path | None = None,
    ) -> None:
        """Load budget policy from an operator-owned path outside Agent code.

        The runtime has no write path for this file.  ``agent_root`` is
        explicit so a supervisor can define the workspace boundary; by
        default the current process directory is treated as that boundary.
        OS ACLs or a secret-store mount must protect the external path in a
        deployment.  A file inside the Agent workspace is rejected even when
        its JSON is otherwise valid.
        """
        path = Path(config_path).expanduser().resolve()
        root = Path(agent_root or Path.cwd()).expanduser().resolve()
        try:
            path.relative_to(root)
        except ValueError:
            pass
        else:
            raise PermissionError("budget config must be outside the Agent workspace")
        if not path.is_file():
            raise FileNotFoundError(path)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid protected budget config: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("protected budget config must be an object")
        period_value = value.get("period")
        period = None
        if period_value is not None:
            if not isinstance(period_value, dict):
                raise ValueError("protected budget period must be an object")
            period = BudgetPeriod(
                str(period_value.get("period_id", "")),
                str(period_value.get("starts_at", "")),
                str(period_value.get("ends_at", "")),
            )
        BudgetAuthority.configure(
            ledger,
            BudgetPolicy(
                hard_cap_minor=value.get("hard_cap_minor"),
                recovery_reserve_minor=value.get("recovery_reserve_minor"),
                currency=value.get("currency", "JPY"),
                period=period,
            ),
        )

    @staticmethod
    def reserve_recovery(
        governor: "BudgetGovernor",
        task: Task,
        resource_id: str,
        *,
        estimated_cost: MoneyAmount | None = None,
        estimated_cost_minor: int | None = None,
        native_units: int | float = 1,
        intent_key: str | None = None,
    ) -> "BudgetReservation":
        """Reserve the protected slice for an explicitly classified Task.

        Normal runtime code must not call ``BudgetGovernor.reserve(...,
        recovery=True)``.  Only this authority-controlled path can provide
        the internal capability required by the Governor.
        """
        if not isinstance(task, Task) or task.task_class is not TaskClass.RECOVERY:
            raise PermissionError("recovery reserve requires the recovery task class on Task")
        return governor.reserve(
            task.task_id,
            resource_id,
            estimated_cost=estimated_cost,
            estimated_cost_minor=estimated_cost_minor,
            recovery=True,
            native_units=native_units,
            intent_key=intent_key,
            _authority=_RECOVERY_RESERVE_TOKEN,
        )


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
    def __init__(self, ledger: ResourceLedger, policy: BudgetPolicy | None = None) -> None:
        self.ledger = ledger
        config = ledger.budget_config()
        period = BudgetPeriod(config["period_id"], config["period_starts_at"], config["period_ends_at"])
        persisted = BudgetPolicy(
            hard_cap_minor=int(config["hard_cap_minor"]),
            recovery_reserve_minor=int(config["recovery_reserve_minor"]),
            currency=str(config["currency"]),
            period=period,
        )
        if policy is not None:
            _validate_policy(policy)
            expected = BudgetPolicy(policy.hard_cap_minor, policy.recovery_reserve_minor, policy.currency, policy.period or period)
            if expected != persisted:
                raise ValueError("provided policy does not match persisted budget")
        self.policy = persisted
        self.period = period
        self.currency = persisted.currency.upper()

    @staticmethod
    def _current_month() -> BudgetPeriod:
        return _current_month()

    def reserve(self, task_id: str, resource_id: str, *, estimated_cost: MoneyAmount | None = None, estimated_cost_minor: int | None = None, recovery: bool = False, native_units: int | float = 1, intent_key: str | None = None, _authority: object | None = None) -> BudgetReservation:
        if recovery and _authority is not _RECOVERY_RESERVE_TOKEN:
            raise PermissionError("recovery reserve requires BudgetAuthority.reserve_recovery")
        if not task_id.strip():
            raise ValueError("task_id is required")
        if estimated_cost is not None and estimated_cost_minor is not None:
            raise ValueError("provide estimated_cost, not both money and minor units")
        if estimated_cost is None and estimated_cost_minor is not None:
            estimated_cost = MoneyAmount(self.currency, estimated_cost_minor)
        if estimated_cost is None:
            raise UnknownPrice(f"price is unknown for resource {resource_id}")
        if isinstance(native_units, bool) or not isinstance(native_units, (int, float)) or not math.isfinite(float(native_units)) or native_units <= 0:
            raise ValueError("native_units must be positive")
        if estimated_cost.currency != self.currency:
            raise BudgetExceeded(f"currency mismatch: budget={self.currency}, request={estimated_cost.currency}")
        resource = self.ledger.get_resource(resource_id)
        if resource["cost_minor"] is not None and resource["price_currency"] != self.currency:
            raise BudgetExceeded(f"currency mismatch: budget={self.currency}, resource={resource['price_currency']}")
        if resource["health"] == "unhealthy":
            raise BudgetExceeded(f"resource is unhealthy: {resource_id}")
        try:
            reservation_id = self.ledger.reserve_budget(task_id=task_id, resource_id=resource_id, amount=estimated_cost, recovery=recovery, period=self.period, normal_limit_minor=self.policy.hard_cap_minor - self.policy.recovery_reserve_minor, recovery_limit_minor=self.policy.recovery_reserve_minor, native_units=native_units, intent_key=intent_key)
        except ValueError as exc:
            message = str(exc)
            if message == "maintenance mode":
                raise MaintenanceActive(message) from exc
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

    def mark_dispatching(self, reservation_id: str) -> None:
        """Persist that the external dispatch boundary is being entered."""
        current = self.ledger.reservation_row(reservation_id)["status"]
        if current == "dispatching":
            return
        if current == "unknown":
            raise BudgetReconciliationRequired("budget reservation requires reconciliation before dispatch")
        self.ledger.transition_budget(reservation_id, to_status="dispatching", expected_from={"prepared"})

    def confirm_no_charge(self, reservation_id: str) -> None:
        """Close a reservation only when the provider outcome proves no charge."""
        self.ledger.transition_budget(reservation_id, to_status="confirmed_no_charge")

    def mark_unknown(self, reservation_id: str) -> None:
        """Hold an uncertain charge until an operator/provider reconciles it."""
        self.ledger.mark_budget_unknown(reservation_id)

    def snapshot(self) -> dict[str, Any]:
        totals = self.ledger.reservation_totals(period_id=self.period.period_id)
        return {**totals, "hard_cap_minor": self.policy.hard_cap_minor, "recovery_reserve_minor": self.policy.recovery_reserve_minor, "normal_available_minor": self.policy.hard_cap_minor - self.policy.recovery_reserve_minor - totals["normal_committed_minor"], "recovery_available_minor": self.policy.recovery_reserve_minor - totals["recovery_committed_minor"], "currency": self.currency, "period_id": self.period.period_id}
