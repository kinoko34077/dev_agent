"""Small, durable metadata helpers for a bounded Commander supervisor run."""

from __future__ import annotations

import json
from typing import Any, Mapping


SUPERVISOR_SCHEMA_VERSION = 1
SUPERVISOR_STATUSES = frozenset(
    {
        "ACTIVE",
        "WAITING_FOR_WORKER",
        "WORKER_RESULT_READY",
        "REVIEWING",
        "INTEGRATING",
        "HUMAN_DECISION_REQUIRED",
        "COMPLETED",
        "BLOCKED",
    }
)
HEARTBEAT_CADENCES = (1, 5, 10, 15)
WAKE_EVENT_LIMIT = 64
DEFAULT_UNCHANGED_CHECK_LIMIT = 2
MAX_UNCHANGED_CHECK_LIMIT = 3
_METRIC_KEYS = (
    "codex_wake_count",
    "codex_review_count",
    "worker_dispatch_count",
    "worker_success_count",
    "worker_retry_count",
    "payload_inline_bytes",
    "payload_reference_bytes",
    "artifact_fetch_count",
)
_FORBIDDEN_EVENT_KEYS = frozenset({"raw_output", "conversation", "payload", "stdout", "stderr", "patch"})


def _json(value: Any, name: str) -> Any:
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be JSON-serializable") from exc
    return value


def _text(value: Any, name: str, *, optional: bool = False, max_length: int = 4000) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"{name} is too long")
    return value


def _nonnegative_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def select_heartbeat_cadence(expected_remaining_seconds: int | float | None) -> int:
    """Choose one of the bounded heartbeat intervals in minutes."""

    if expected_remaining_seconds is None:
        return 15
    if isinstance(expected_remaining_seconds, bool) or not isinstance(expected_remaining_seconds, (int, float)):
        raise ValueError("expected_remaining_seconds must be numeric or None")
    if expected_remaining_seconds < 0:
        raise ValueError("expected_remaining_seconds must be non-negative")
    if expected_remaining_seconds <= 300:
        return 1
    if expected_remaining_seconds <= 1200:
        return 5
    if expected_remaining_seconds <= 3600:
        return 10
    return 15


def _next_cadence(current: int, unchanged_count: int, limit: int) -> int:
    if unchanged_count < limit:
        return current
    try:
        position = HEARTBEAT_CADENCES.index(current)
    except ValueError as exc:
        raise ValueError("cadence_minutes is unsupported") from exc
    return HEARTBEAT_CADENCES[min(position + 1, len(HEARTBEAT_CADENCES) - 1)]


def normalize_supervisor_metadata(value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Normalize optional plan metadata without accepting raw Worker output."""

    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise TypeError("supervisor metadata must be an object")
    status = _text(value.get("status", "ACTIVE"), "supervisor.status")
    if status not in SUPERVISOR_STATUSES:
        raise ValueError(f"unsupported supervisor.status: {status}")
    cadence = value.get("cadence_minutes", 15)
    if cadence not in HEARTBEAT_CADENCES:
        raise ValueError("supervisor.cadence_minutes must be one of 1, 5, 10, 15")
    limit = value.get("unchanged_check_limit", DEFAULT_UNCHANGED_CHECK_LIMIT)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_UNCHANGED_CHECK_LIMIT:
        raise ValueError("supervisor.unchanged_check_limit must be between 1 and 3")
    unchanged = _nonnegative_int(value.get("unchanged_check_count", 0), "supervisor.unchanged_check_count")
    roadmap = value.get("roadmap_reference", {})
    if not isinstance(roadmap, Mapping):
        raise TypeError("supervisor.roadmap_reference must be an object")
    _json(roadmap, "supervisor.roadmap_reference")
    next_action = _text(value.get("next_action", "advance"), "supervisor.next_action")
    deadline = _text(value.get("overall_deadline"), "supervisor.overall_deadline", optional=True)
    wake_events = value.get("wake_events", [])
    if not isinstance(wake_events, list):
        raise TypeError("supervisor.wake_events must be a list")
    normalized_events: list[dict[str, Any]] = []
    for item in wake_events[-WAKE_EVENT_LIMIT:]:
        if not isinstance(item, Mapping):
            raise TypeError("supervisor wake events must be objects")
        if _FORBIDDEN_EVENT_KEYS.intersection(item):
            raise ValueError("supervisor wake events must not contain raw output")
        kind = _text(item.get("kind"), "supervisor wake kind", max_length=128)
        assert kind is not None
        record: dict[str, Any] = {"kind": kind}
        for key in ("task_id", "attempt_id", "digest", "occurred_at"):
            if key in item and item[key] is not None:
                record[key] = _text(item[key], f"supervisor wake {key}", max_length=256)
        normalized_events.append(record)
    metrics = value.get("metrics", {})
    if not isinstance(metrics, Mapping):
        raise TypeError("supervisor.metrics must be an object")
    normalized_metrics = {
        key: _nonnegative_int(metrics.get(key, 0), f"supervisor.metrics.{key}")
        for key in _METRIC_KEYS
    }
    return {
        "schema_version": SUPERVISOR_SCHEMA_VERSION,
        "status": status,
        "roadmap_reference": dict(roadmap),
        "roadmap_position": _text(value.get("roadmap_position"), "supervisor.roadmap_position", optional=True),
        "cadence_minutes": cadence,
        "unchanged_check_limit": limit,
        "unchanged_check_count": unchanged,
        "overall_deadline": deadline,
        "next_action": next_action,
        "wake_events": normalized_events,
        "metrics": normalized_metrics,
    }


def record_wake(
    value: Mapping[str, Any],
    *,
    kind: str,
    task_id: str | None = None,
    attempt_id: str | None = None,
    digest: str | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Append one compact wake event, deduplicated by its stable identity."""

    metadata = normalize_supervisor_metadata(value)
    event: dict[str, Any] = {"kind": _text(kind, "kind", max_length=128)}
    for key, item in (("task_id", task_id), ("attempt_id", attempt_id), ("digest", digest)):
        if item is not None:
            event[key] = _text(item, key, max_length=256)
    if digest is not None and (len(digest) != 64 or any(char not in "0123456789abcdefABCDEF" for char in digest)):
        raise ValueError("digest must be a SHA-256 hex digest")
    if occurred_at is not None:
        event["occurred_at"] = _text(occurred_at, "occurred_at", max_length=80)
    identity = tuple(event.get(key) for key in ("kind", "task_id", "attempt_id", "digest"))
    existing = [tuple(item.get(key) for key in ("kind", "task_id", "attempt_id", "digest")) for item in metadata["wake_events"]]
    if identity not in existing:
        metadata["wake_events"] = [*metadata["wake_events"], event][-WAKE_EVENT_LIMIT:]
    return metadata


def advance_heartbeat(
    value: Mapping[str, Any],
    *,
    unchanged: bool,
    expected_remaining_seconds: int | float | None = None,
) -> dict[str, Any]:
    """Advance bounded unchanged checks and select the next cadence."""

    metadata = normalize_supervisor_metadata(value)
    count = metadata["unchanged_check_count"] + 1 if unchanged else 0
    metadata["unchanged_check_count"] = count
    if unchanged:
        metadata["cadence_minutes"] = _next_cadence(
            metadata["cadence_minutes"], count, metadata["unchanged_check_limit"]
        )
    elif expected_remaining_seconds is not None:
        metadata["cadence_minutes"] = select_heartbeat_cadence(expected_remaining_seconds)
    return normalize_supervisor_metadata(metadata)


__all__ = [
    "DEFAULT_UNCHANGED_CHECK_LIMIT",
    "HEARTBEAT_CADENCES",
    "MAX_UNCHANGED_CHECK_LIMIT",
    "SUPERVISOR_SCHEMA_VERSION",
    "SUPERVISOR_STATUSES",
    "advance_heartbeat",
    "normalize_supervisor_metadata",
    "record_wake",
    "select_heartbeat_cadence",
]
