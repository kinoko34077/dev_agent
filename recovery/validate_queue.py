"""Read-only validation for the Phase 6 scheduler queue."""

from __future__ import annotations

import math
from pathlib import Path
import sqlite3


SCHEMA_VERSION = 5
VALID_STATES = {"queued", "leased", "waiting", "completed", "failed"}
REQUIRED_TABLES = {"queue_items", "scheduler_control", "scheduler_schema_meta"}


def validate_scheduler_queue(path: str | Path) -> tuple[bool, str]:
    """Validate queue schema, lease shape, and persisted retry ceilings."""
    database = Path(path).expanduser()
    try:
        with sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            missing = REQUIRED_TABLES - tables
            if missing:
                return False, f"scheduler queue missing tables: {', '.join(sorted(missing))}"
            version_row = connection.execute("SELECT value FROM scheduler_schema_meta WHERE key='schema_version'").fetchone()
            if version_row is None or version_row[0] != str(SCHEMA_VERSION):
                return False, f"unsupported or stale scheduler queue schema version: {version_row[0] if version_row else 'missing'}"
            columns = {row[1] for row in connection.execute("PRAGMA table_info(queue_items)")}
            required_columns = {"task_id", "priority", "run_at", "state", "lease_owner", "lease_until", "lease_token", "state_version", "attempts", "max_attempts", "wake_at", "wake_reason", "claim_count", "execution_attempts", "max_execution_attempts"}
            if not required_columns <= columns:
                return False, "scheduler queue schema lacks lease or max_attempts columns"
            control = connection.execute("SELECT maintenance FROM scheduler_control WHERE id=1").fetchone()
            if control is not None and control[0] not in (0, 1):
                return False, "scheduler queue maintenance flag is invalid"
            for row in connection.execute("SELECT task_id, run_at, state, lease_owner, lease_until, lease_token, state_version, attempts, max_attempts, wake_at, wake_reason, claim_count, execution_attempts, max_execution_attempts FROM queue_items"):
                task_id, run_at, state, owner, lease_until, token, state_version, attempts, max_attempts, wake_at, wake_reason, claim_count, execution_attempts, max_execution_attempts = row
                if not isinstance(task_id, str) or not task_id.strip() or state not in VALID_STATES:
                    return False, f"scheduler queue item identity or state is invalid: {task_id}"
                if not isinstance(run_at, (int, float)) or not math.isfinite(float(run_at)):
                    return False, f"scheduler queue run_at is invalid: {task_id}"
                if isinstance(state_version, bool) or not isinstance(state_version, int) or state_version <= 0:
                    return False, f"scheduler queue state_version is invalid: {task_id}"
                if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
                    return False, f"scheduler queue attempts are invalid: {task_id}"
                if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts <= 0:
                    return False, f"scheduler queue max_attempts is invalid: {task_id}"
                if isinstance(claim_count, bool) or not isinstance(claim_count, int) or claim_count < 0 or claim_count < attempts:
                    return False, f"scheduler queue claim_count is invalid: {task_id}"
                if isinstance(execution_attempts, bool) or not isinstance(execution_attempts, int) or execution_attempts < 0:
                    return False, f"scheduler queue execution_attempts are invalid: {task_id}"
                if isinstance(max_execution_attempts, bool) or not isinstance(max_execution_attempts, int) or max_execution_attempts <= 0:
                    return False, f"scheduler queue max_execution_attempts is invalid: {task_id}"
                if wake_at is not None and (not isinstance(wake_at, (int, float)) or not math.isfinite(float(wake_at))):
                    return False, f"scheduler queue wake_at is invalid: {task_id}"
                if wake_reason is not None and (not isinstance(wake_reason, str) or not wake_reason.strip()):
                    return False, f"scheduler queue wake_reason is invalid: {task_id}"
                if state == "waiting" and (wake_at is None) != (wake_reason is None):
                    return False, f"scheduler queue wake metadata is incomplete: {task_id}"
                if state != "waiting" and (wake_at is not None or wake_reason is not None):
                    return False, f"scheduler queue non-waiting item retains wake fields: {task_id}"
                if state == "leased":
                    if not isinstance(owner, str) or not owner.strip() or not isinstance(token, str) or not token.strip() or not isinstance(lease_until, (int, float)) or not math.isfinite(float(lease_until)):
                        return False, f"scheduler queue lease is invalid: {task_id}"
                elif owner is not None or lease_until is not None or token is not None:
                    return False, f"scheduler queue non-leased item retains lease fields: {task_id}"
            return True, "Phase 6 scheduler queue is readable"
    except (OSError, sqlite3.DatabaseError) as exc:
        return False, f"scheduler queue validation failed: {exc}"


__all__ = ["validate_scheduler_queue"]
