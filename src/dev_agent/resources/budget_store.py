"""Atomic budget and native-resource reservation persistence."""

from __future__ import annotations

from datetime import datetime, timezone
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid4


_BUDGET_TRANSITIONS = {
    "prepared": {"dispatching", "unknown", "confirmed_no_charge"},
    "dispatching": {"unknown", "reconciled", "confirmed_no_charge"},
    "unknown": {"reconciled", "confirmed_no_charge"},
    "reconciled": set(),
    "confirmed_no_charge": set(),
    "released": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BudgetReservationStore:
    """Own budget-reservation SQL while sharing the ledger transaction owner."""

    def __init__(self, connection: sqlite3.Connection, lock: RLock) -> None:
        self._connection = connection
        self._lock = lock

    def _maintenance_enabled(self) -> bool:
        row = self._connection.execute("SELECT maintenance FROM resource_control WHERE id=1").fetchone()
        return bool(row and row[0])

    def reservation_row(self, reservation_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute("SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if row is None:
                raise KeyError(reservation_id)
            return dict(row)

    def reservation_totals(self, *, period_id: str | None = None) -> dict[str, int]:
        with self._lock:
            query = "SELECT recovery, status, COALESCE(actual_minor, estimated_minor) AS amount FROM budget_reservations WHERE status IN ('prepared', 'dispatching', 'unknown', 'reconciled')"
            params: tuple[str, ...] = ()
            if period_id is not None:
                query += " AND period_id=?"
                params = (period_id,)
            rows = self._connection.execute(query, params).fetchall()
            normal_committed = sum(int(row["amount"]) for row in rows if not row["recovery"])
            recovery_committed = sum(int(row["amount"]) for row in rows if row["recovery"])
            active = sum(1 for row in rows if row["status"] in {"prepared", "dispatching", "unknown"})
            return {
                "normal_committed_minor": normal_committed,
                "recovery_committed_minor": recovery_committed,
                "active_reservations": active,
            }

    def reserve_budget(
        self,
        *,
        task_id: str,
        resource_id: str,
        amount: Any,
        recovery: bool,
        period: Any,
        normal_limit_minor: int,
        recovery_limit_minor: int,
        native_units: int | float = 1,
        intent_key: str | None = None,
    ) -> str:
        """Atomically check and create a budget and resource reservation."""
        if intent_key is not None and (not isinstance(intent_key, str) or not intent_key.strip()):
            raise ValueError("intent_key must be a non-empty string or None")
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if self._maintenance_enabled():
                    raise ValueError("maintenance mode")
                if intent_key is not None:
                    existing = self._connection.execute("SELECT * FROM budget_reservations WHERE intent_key=?", (intent_key,)).fetchone()
                    if existing is not None:
                        if (
                            existing["task_id"] != task_id
                            or existing["resource_id"] != resource_id
                            or int(existing["estimated_minor"]) != amount.minor_units
                            or bool(existing["recovery"]) != bool(recovery)
                            or existing["period_id"] != period.period_id
                            or existing["currency"] != amount.currency
                        ):
                            raise ValueError("intent key is bound to a different budget reservation")
                        if existing["status"] in {"reconciled", "confirmed_no_charge", "released"}:
                            raise ValueError("intent key is bound to a terminal budget reservation")
                        resource_reservation = self._connection.execute("SELECT native_units, status FROM resource_reservations WHERE reservation_id=?", (existing["reservation_id"],)).fetchone()
                        if resource_reservation is None or resource_reservation["status"] != "reserved" or float(resource_reservation["native_units"]) != float(native_units):
                            raise ValueError("intent key is bound to an invalid resource reservation")
                        self._connection.commit()
                        return existing["reservation_id"]
                totals = self.reservation_totals(period_id=period.period_id)
                resource = self._connection.execute("SELECT available, health, price_currency FROM resources WHERE resource_id=?", (resource_id,)).fetchone()
                if resource is None:
                    raise KeyError(resource_id)
                if resource["health"] == "unhealthy":
                    raise ValueError(f"resource is unhealthy: {resource_id}")
                if resource["price_currency"] is not None and resource["price_currency"] != amount.currency:
                    raise ValueError(f"currency mismatch for resource: {resource_id}")
                used = self._connection.execute("SELECT COALESCE(SUM(native_units), 0) FROM resource_reservations WHERE resource_id=? AND status='reserved'", (resource_id,)).fetchone()[0]
                if float(used) + float(native_units) > float(resource["available"]):
                    raise ValueError(f"resource capacity exceeded: {resource_id}")
                remaining = (recovery_limit_minor if recovery else normal_limit_minor) - (totals["recovery_committed_minor"] if recovery else totals["normal_committed_minor"])
                if amount.minor_units > remaining:
                    raise ValueError(f"budget exceeded: requested {amount.minor_units}, remaining {remaining}")
                reservation_id = str(uuid4())
                self._connection.execute(
                    "INSERT INTO budget_reservations(reservation_id, task_id, resource_id, intent_key, estimated_minor, actual_minor, recovery, status, created_at, reconciled_at, period_id, currency) VALUES (?, ?, ?, ?, ?, NULL, ?, 'prepared', ?, NULL, ?, ?)",
                    (reservation_id, task_id, resource_id, intent_key, amount.minor_units, int(recovery), _now(), period.period_id, amount.currency),
                )
                self._connection.execute("INSERT INTO resource_reservations(reservation_id, resource_id, native_units, status) VALUES (?, ?, ?, 'reserved')", (reservation_id, resource_id, native_units))
                self._connection.commit()
                return reservation_id
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    def reconcile_budget(
        self,
        reservation_id: str,
        *,
        actual: Any,
        period: Any,
        normal_limit_minor: int,
        recovery_limit_minor: int,
    ) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute("SELECT * FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
                if row is None:
                    raise KeyError(reservation_id)
                if row["status"] not in {"prepared", "dispatching", "unknown"}:
                    raise ValueError(f"reservation is not active: {reservation_id}")
                if row["period_id"] != period.period_id or row["currency"] != actual.currency:
                    raise ValueError("reservation does not belong to current budget period or currency")
                totals = self.reservation_totals(period_id=period.period_id)
                current_total = totals["recovery_committed_minor"] if row["recovery"] else totals["normal_committed_minor"]
                current_total -= int(row["estimated_minor"])
                limit = recovery_limit_minor if row["recovery"] else normal_limit_minor
                if current_total + actual.minor_units > limit:
                    raise ValueError("actual usage exceeds the protected budget")
                self._connection.execute("UPDATE budget_reservations SET actual_minor=?, status='reconciled', reconciled_at=? WHERE reservation_id=?", (actual.minor_units, _now(), reservation_id))
                self._connection.execute("UPDATE resource_reservations SET status='released' WHERE reservation_id=?", (reservation_id,))
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def transition_budget(self, reservation_id: str, *, to_status: str, expected_from: set[str] | None = None) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute("SELECT status FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
                if row is None:
                    raise KeyError(reservation_id)
                current = row["status"]
                if expected_from is not None and current not in expected_from:
                    raise ValueError(f"budget reservation is not in an expected state: {current}")
                if to_status not in _BUDGET_TRANSITIONS or to_status not in _BUDGET_TRANSITIONS.get(current, set()):
                    raise ValueError(f"invalid budget reservation transition: {current} -> {to_status}")
                terminal = to_status in {"reconciled", "confirmed_no_charge", "released"}
                self._connection.execute("UPDATE budget_reservations SET status=?, reconciled_at=CASE WHEN ? THEN ? ELSE reconciled_at END WHERE reservation_id=?", (to_status, int(terminal), _now(), reservation_id))
                if terminal:
                    self._connection.execute("UPDATE resource_reservations SET status='released' WHERE reservation_id=?", (reservation_id,))
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise

    def release_budget(self, reservation_id: str) -> None:
        self.transition_budget(reservation_id, to_status="confirmed_no_charge", expected_from={"prepared", "dispatching"})

    def mark_budget_unknown(self, reservation_id: str) -> None:
        with self._lock:
            row = self._connection.execute("SELECT status FROM budget_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if row is None:
                raise KeyError(reservation_id)
            if row["status"] == "unknown":
                return
        self.transition_budget(reservation_id, to_status="unknown", expected_from={"prepared", "dispatching"})


__all__ = ["BudgetReservationStore"]
