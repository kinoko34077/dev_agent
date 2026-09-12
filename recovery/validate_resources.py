"""Read-only validation for the Phase 6 resource ledger."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import math
import json


SCHEMA_VERSION = 10


def validate_resource_ledger(path: str | Path) -> tuple[bool, str]:
    database = Path(path).expanduser()
    try:
        with sqlite3.connect(database) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"resources", "resource_observations", "quota_observations", "budget_config", "budget_reservations", "resource_reservations", "resource_schema_meta", "resource_repairs"}
            missing = required - tables
            if missing:
                return False, f"resource ledger missing tables: {', '.join(sorted(missing))}"
            version_row = connection.execute("SELECT value FROM resource_schema_meta WHERE key='schema_version'").fetchone()
            if version_row is None:
                return False, "resource ledger schema version is missing"
            try:
                schema_version = int(version_row[0])
            except (TypeError, ValueError):
                return False, "resource ledger schema version is invalid"
            if schema_version != SCHEMA_VERSION:
                return False, f"unsupported or stale resource ledger schema version: {schema_version}"
            resource_columns = {row[1] for row in connection.execute("PRAGMA table_info(resources)")}
            if not {"quota_domain", "quota_remaining_ratio", "latency_ewma_ms", "failure_ewma", "inflight", "concurrency_limit"} <= resource_columns:
                return False, "resource ledger resources table lacks operational observation columns"
            quota_columns = {row[1] for row in connection.execute("PRAGMA table_info(quota_observations)")}
            if not {"resource_id", "quota_domain", "unit", "limit_value", "remaining_value", "consumed_value", "authority", "metric", "window", "reset_source", "blocked_until", "block_reason", "request_limit", "request_remaining", "token_limit", "token_remaining", "confidence", "observed_at", "source"} <= quota_columns:
                return False, "resource ledger quota observations table is incomplete"
            reservation_columns = {row[1] for row in connection.execute("PRAGMA table_info(budget_reservations)")}
            if "intent_key" not in reservation_columns:
                return False, "resource ledger budget reservations lack intent_key binding"
            budget = connection.execute("SELECT hard_cap_minor, recovery_reserve_minor FROM budget_config WHERE id=1").fetchone()
            if budget is None or budget[0] < 0 or budget[1] < 0 or budget[1] > budget[0]:
                return False, "resource ledger budget configuration is invalid"
            resources = connection.execute("SELECT resource_id, quota_domain, capacity, available, confidence, health FROM resources").fetchall()
            resource_domains: dict[str, str | None] = {}
            for resource_id, quota_domain, capacity, available, confidence, health in resources:
                if not all(math.isfinite(float(value)) for value in (capacity, available, confidence)) or capacity < 0 or available < 0 or available > capacity or confidence < 0 or confidence > 1 or health not in {"healthy", "degraded", "unhealthy", "unknown"}:
                    return False, f"resource record is invalid: {resource_id}"
                resource_domains[resource_id] = quota_domain
            legacy = legacy_resource_metadata(path)
            if legacy:
                return False, f"legacy resource metadata requires explicit repair: {', '.join(legacy)}"
            quota_observations = connection.execute(
                """SELECT resource_id, quota_domain, unit, limit_value,
                          remaining_value, consumed_value, authority, metric,
                          window, reset_source, blocked_until, block_reason,
                          request_limit, request_remaining, token_limit,
                          token_remaining, confidence, source
                   FROM quota_observations"""
            ).fetchall()
            for resource_id, quota_domain, unit, limit_value, remaining_value, consumed_value, authority, metric, window, reset_source, blocked_until, block_reason, request_limit, request_remaining, token_limit, token_remaining, confidence, source in quota_observations:
                if resource_id not in resource_domains or resource_domains[resource_id] != quota_domain:
                    return False, f"quota observation has an invalid resource/domain binding: {resource_id}"
                if unit not in {"requests", "tokens", "neurons"}:
                    return False, f"quota observation unit is invalid: {resource_id}"
                if any(value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) or value < 0) for value in (limit_value, remaining_value, consumed_value)):
                    return False, f"quota observation generic values are invalid: {resource_id}"
                if limit_value is not None and remaining_value is not None and remaining_value > limit_value:
                    return False, f"quota observation remaining exceeds limit: {resource_id}"
                if not isinstance(authority, str) or not authority.strip():
                    return False, f"quota observation authority is invalid: {resource_id}"
                if not isinstance(metric, str) or not metric.strip() or len(metric) > 64:
                    return False, f"quota observation metric is invalid: {resource_id}"
                if window not in {"unknown", "minute", "day", "month"}:
                    return False, f"quota observation window is invalid: {resource_id}"
                for field_name, field_value in (("reset_source", reset_source), ("blocked_until", blocked_until), ("block_reason", block_reason)):
                    if field_value is not None and (not isinstance(field_value, str) or not field_value.strip() or len(field_value) > 256):
                        return False, f"quota observation {field_name} is invalid: {resource_id}"
                if any(value is not None and (not isinstance(value, int) or value < 0) for value in (request_limit, request_remaining, token_limit, token_remaining)):
                    return False, f"quota observation has invalid limits: {resource_id}"
                if request_limit is not None and request_remaining is not None and request_remaining > request_limit:
                    return False, f"quota observation request remaining exceeds limit: {resource_id}"
                if token_limit is not None and token_remaining is not None and token_remaining > token_limit:
                    return False, f"quota observation token remaining exceeds limit: {resource_id}"
                if not math.isfinite(float(confidence)) or confidence < 0 or confidence > 1 or not isinstance(source, str) or not source.strip():
                    return False, f"quota observation metadata is invalid: {resource_id}"
            reservations = connection.execute("SELECT reservation_id, task_id, resource_id, intent_key, estimated_minor, actual_minor, recovery, status FROM budget_reservations").fetchall()
            valid_statuses = {"prepared", "dispatching", "unknown", "reconciled", "confirmed_no_charge", "released"}
            budget_records = {row[0]: row for row in reservations}
            intent_keys: set[str] = set()
            for reservation_id, task_id, resource_id, intent_key, estimated, actual, recovery, status in reservations:
                if intent_key is not None:
                    if not isinstance(intent_key, str) or not intent_key.strip() or intent_key in intent_keys:
                        return False, f"budget reservation intent binding is invalid: {reservation_id}"
                    intent_keys.add(intent_key)
                if not isinstance(task_id, str) or not task_id.strip() or not isinstance(resource_id, str) or not resource_id.strip() or estimated < 0 or (actual is not None and actual < 0) or recovery not in {0, 1} or status not in valid_statuses:
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
                budget_status = budget_records[reservation_id][7]
                expected_status = "reserved" if budget_status in {"prepared", "dispatching", "unknown", "reserved"} else "released"
                if resource_id not in resource_ids or not math.isfinite(float(native_units)) or native_units <= 0 or status not in native_statuses or status != expected_status:
                    return False, f"resource reservation is invalid: {reservation_id}"
    except (OSError, sqlite3.DatabaseError) as exc:
        return False, f"resource ledger validation failed: {exc}"
    return True, "Phase 6 resource ledger is readable"


def legacy_resource_metadata(path: str | Path) -> list[str]:
    """Return cloud Resource ids that do not satisfy the current authority schema.

    Recovery stays runtime-independent and performs a structural schema check.
    It does not infer billing facts; the Operation repair command handles that.
    """

    database = Path(path).expanduser()
    # Local-only providers are exempt from cloud authority schema requirements.
    # Derived from the single authority source — provider_authority_constants —
    # so adding a new local provider updates runtime and recovery together.
    from dev_agent.resources.provider_authority_constants import LOCAL_PROVIDER_IDS as _pac
    local_only_providers = _pac
    try:
        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                "SELECT resource_id, provider_id, cost_minor, metadata_json FROM resources"
            ).fetchall()
    except (OSError, sqlite3.DatabaseError):
        return []
    _VALID_BILLING_MODES = {"free_fixed", "recurring_allowance", "recurring_credit", "paid", "unknown"}
    _VALID_OVERAGE_POLICIES = {"hard_stop", "billable", "unknown"}
    legacy: list[str] = []
    for resource_id, provider_id, cost_minor, metadata_json in rows:
        if provider_id in local_only_providers:
            continue
        try:
            metadata = json.loads(metadata_json) if isinstance(metadata_json, str) else {}
        except json.JSONDecodeError:
            metadata = {}
        is_legacy = False
        # All remote resources must carry the qualification marker.
        if metadata.get("qualification_required") is not True:
            is_legacy = True
        # Remote resource must not carry a local-only privacy profile.
        if metadata.get("privacy_profile") == "local_only":
            is_legacy = True
        billing_mode = metadata.get("billing_mode")
        if billing_mode is not None and billing_mode not in _VALID_BILLING_MODES:
            is_legacy = True
        overage_policy = metadata.get("overage_policy")
        if overage_policy is not None and overage_policy not in _VALID_OVERAGE_POLICIES:
            is_legacy = True
        # trusted_catalog resources require the full billing contract schema.
        if metadata.get("billing_authority") == "trusted_catalog":
            if not isinstance(metadata.get("provider_binding_id"), str) or not metadata["provider_binding_id"].strip():
                is_legacy = True
            if not isinstance(metadata.get("model_id"), str) or not metadata["model_id"].strip():
                is_legacy = True
            if not isinstance(metadata.get("billing_expires_at"), str):
                is_legacy = True
            if billing_mode not in _VALID_BILLING_MODES:
                is_legacy = True
            if overage_policy not in _VALID_OVERAGE_POLICIES:
                is_legacy = True
        if is_legacy:
            legacy.append(str(resource_id))
    return sorted(legacy)


__all__ = ["legacy_resource_metadata", "validate_resource_ledger"]
