"""Read-only validation for the Phase 6 resource ledger."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import math


def validate_resource_ledger(path: str | Path) -> tuple[bool, str]:
    database = Path(path).expanduser()
    try:
        with sqlite3.connect(database) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"resources", "resource_observations", "budget_config", "budget_reservations", "resource_reservations"}
            missing = required - tables
            if missing:
                return False, f"resource ledger missing tables: {', '.join(sorted(missing))}"
            budget = connection.execute("SELECT hard_cap_minor, recovery_reserve_minor FROM budget_config WHERE id=1").fetchone()
            if budget is None or budget[0] < 0 or budget[1] < 0 or budget[1] > budget[0]:
                return False, "resource ledger budget configuration is invalid"
            resources = connection.execute("SELECT resource_id, capacity, available, confidence, health FROM resources").fetchall()
            for resource_id, capacity, available, confidence, health in resources:
                if not all(math.isfinite(float(value)) for value in (capacity, available, confidence)) or capacity < 0 or available < 0 or available > capacity or confidence < 0 or confidence > 1 or health not in {"healthy", "degraded", "unhealthy", "unknown"}:
                    return False, f"resource record is invalid: {resource_id}"
            reservations = connection.execute("SELECT reservation_id, estimated_minor, actual_minor, recovery, status FROM budget_reservations").fetchall()
            valid_statuses = {"prepared", "dispatching", "unknown", "reconciled", "confirmed_no_charge", "released"}
            budget_records = {row[0]: row for row in reservations}
            for reservation_id, estimated, actual, recovery, status in reservations:
                if estimated < 0 or (actual is not None and actual < 0) or recovery not in {0, 1} or status not in valid_statuses:
                    return False, f"budget reservation is invalid: {reservation_id}"
            native_reservations = connection.execute(
                "SELECT reservation_id, resource_id, native_units, status FROM resource_reservations"
            ).fetchall()
            native_statuses = {"reserved", "released"}
            budget_ids = set(budget_records)
            resource_ids = {row[0] for row in resources}
            native_ids = {row[0] for row in native_reservations}
            if native_ids != budget_ids:
                return False, "budget and native resource reservation records are inconsistent"
            for reservation_id, resource_id, native_units, status in native_reservations:
                budget_status = budget_records[reservation_id][4]
                expected_status = "reserved" if budget_status in {"prepared", "dispatching", "unknown", "reserved"} else "released"
                if resource_id not in resource_ids or not math.isfinite(float(native_units)) or native_units <= 0 or status not in native_statuses or status != expected_status:
                    return False, f"resource reservation is invalid: {reservation_id}"
    except (OSError, sqlite3.DatabaseError) as exc:
        return False, f"resource ledger validation failed: {exc}"
    return True, "Phase 6 resource ledger is readable"


__all__ = ["validate_resource_ledger"]
