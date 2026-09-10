"""Durable, host-observed metrics for the development Worker Farm.

This module is intentionally separate from the production runtime.  It stores
only the result of host verification, never model test claims or raw provider
responses, so later routing experiments can use measured history without
turning the metrics file into a second source of task state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid5, NAMESPACE_URL


class WorkerMetricsError(ValueError):
    """A host metric record is malformed or cannot be safely persisted."""


def _text(value: Any, name: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise WorkerMetricsError(f"{name} must be a non-empty string of at most {max_length} characters")
    return value.strip()


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise WorkerMetricsError(f"{name} must be an integer >= {minimum}")
    return value


def _optional_integer(value: Any, name: str, *, minimum: int = 0) -> int | None:
    if value is None:
        return None
    return _integer(value, name, minimum=minimum)


def _scalar(value: Any) -> int | float | str | bool | None:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return value[:512]
    return None


def _safe_json(value: Any, *, depth: int = 0) -> Any:
    """Normalize a bounded scalar/object/list tree for local metrics only."""

    if depth > 3:
        return None
    scalar = _scalar(value)
    if scalar is not None:
        return scalar
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 128:
                continue
            normalized = _safe_json(item, depth=depth + 1)
            if normalized is not None:
                result[key] = normalized
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_json(item, depth=depth + 1) for item in list(value)[:64]]
    return None


def _host_test_summary(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise WorkerMetricsError("host_verified_tests must be a list")
    summary: list[dict[str, Any]] = []
    for item in value[:64]:
        if not isinstance(item, Mapping):
            raise WorkerMetricsError("host_verified_tests entries must be objects")
        command = _text(item.get("command"), "host test command", max_length=512)
        exit_code = item.get("exit_code")
        if exit_code is not None:
            exit_code = _integer(exit_code, "host test exit_code", minimum=-2147483648)
        passed = item.get("passed")
        if not isinstance(passed, bool):
            raise WorkerMetricsError("host test passed must be a boolean")
        timed_out = item.get("timed_out", False)
        if not isinstance(timed_out, bool):
            raise WorkerMetricsError("host test timed_out must be a boolean")
        summary.append({"command": command, "exit_code": exit_code, "passed": passed, "timed_out": timed_out})
    return summary


def _recorded_at(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    candidate = _text(value, "recorded_at", max_length=80)
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise WorkerMetricsError("recorded_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise WorkerMetricsError("recorded_at must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


class WorkerMetricsStore:
    """Small SQLite store for host-verified Worker observations."""

    SCHEMA_VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        with self._lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS worker_metrics_schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS worker_metrics (
                    metric_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    provider_binding_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    intelligence_tier TEXT,
                    task_type TEXT NOT NULL,
                    elapsed_ms INTEGER NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    retry_count INTEGER NOT NULL,
                    usage_json TEXT NOT NULL,
                    quota_observation_json TEXT NOT NULL,
                    host_tests_json TEXT NOT NULL,
                    host_verified INTEGER NOT NULL CHECK(host_verified IN (0, 1)),
                    host_tests_passed INTEGER NOT NULL CHECK(host_tests_passed IN (0, 1)),
                    result_accepted INTEGER NOT NULL CHECK(result_accepted IN (0, 1)),
                    codex_correction_chars INTEGER,
                    recorded_at TEXT NOT NULL,
                    UNIQUE(task_id, request_id)
                );
                CREATE INDEX IF NOT EXISTS idx_worker_metrics_routing
                    ON worker_metrics(task_type, provider_binding_id, model_id, intelligence_tier);
                """
            )
            row = self.connection.execute(
                "SELECT value FROM worker_metrics_schema_meta WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                self.connection.execute(
                    "INSERT INTO worker_metrics_schema_meta(key, value) VALUES ('schema_version', ?)",
                    (str(self.SCHEMA_VERSION),),
                )
            elif int(row[0]) != self.SCHEMA_VERSION:
                raise WorkerMetricsError(f"unsupported worker metrics schema version: {row[0]}")
            self.connection.commit()

    def record(
        self,
        *,
        manifest: Mapping[str, Any],
        result: Mapping[str, Any],
        recorded_at: str | None = None,
    ) -> dict[str, Any]:
        """Upsert one host-verified result and return its normalized record.

        A proposal is not evidence.  ``host_verified`` and all acceptance
        fields are required so this method cannot accidentally turn a model
        claim into routing history.
        """

        if not isinstance(manifest, Mapping) or not isinstance(result, Mapping):
            raise WorkerMetricsError("manifest and result must be objects")
        task_id = _text(manifest.get("task_id"), "manifest task_id", max_length=128)
        metrics = result.get("worker_metrics")
        if not isinstance(metrics, Mapping):
            raise WorkerMetricsError("result worker_metrics must be an object")
        if metrics.get("host_verified") is not True:
            raise WorkerMetricsError("only host-verified results can be recorded")
        host_tests_passed = metrics.get("host_tests_passed")
        result_accepted = metrics.get("result_accepted")
        if not isinstance(host_tests_passed, bool) or not isinstance(result_accepted, bool):
            raise WorkerMetricsError("host verification acceptance fields must be booleans")
        request_id = _text(metrics.get("request_id"), "request_id", max_length=256)
        provider_id = _text(metrics.get("provider_id"), "provider_id")
        binding_id = _text(metrics.get("provider_binding_id"), "provider_binding_id")
        model_id = _text(metrics.get("model_id"), "model_id")
        tier_value = metrics.get("intelligence_tier")
        tier = _text(tier_value, "intelligence_tier") if tier_value is not None else None
        task_type = _text(manifest.get("task_type", "unspecified"), "task_type", max_length=64)
        elapsed_ms = _integer(metrics.get("elapsed_ms"), "elapsed_ms")
        attempt_count = _integer(metrics.get("attempt_count"), "attempt_count", minimum=1)
        codex_correction_chars = _optional_integer(metrics.get("codex_correction_chars"), "codex_correction_chars")
        usage = _safe_json(metrics.get("usage", {}))
        if not isinstance(usage, dict):
            usage = {}
        quota = usage.get("quota_observation", {})
        if not isinstance(quota, Mapping):
            quota = {}
        host_tests = _host_test_summary(result.get("host_verified_tests", []))
        normalized = {
            "metric_id": str(uuid5(NAMESPACE_URL, f"dev_agent.devfarm.metrics/{task_id}/{request_id}")),
            "task_id": task_id,
            "request_id": request_id,
            "provider_id": provider_id,
            "provider_binding_id": binding_id,
            "model_id": model_id,
            "intelligence_tier": tier,
            "task_type": task_type,
            "elapsed_ms": elapsed_ms,
            "attempt_count": attempt_count,
            "retry_count": max(0, attempt_count - 1),
            "usage": usage,
            "quota_observation": dict(_safe_json(quota) if isinstance(_safe_json(quota), dict) else {}),
            "host_verified": True,
            "host_tests_passed": host_tests_passed,
            "host_test_count": len(host_tests),
            "host_tests": host_tests,
            "result_accepted": result_accepted,
            "codex_correction_chars": codex_correction_chars,
            "recorded_at": _recorded_at(recorded_at),
        }
        with self._lock:
            self.connection.execute(
                """INSERT INTO worker_metrics(
                       metric_id, task_id, request_id, provider_id,
                       provider_binding_id, model_id, intelligence_tier,
                       task_type, elapsed_ms, attempt_count, retry_count,
                       usage_json, quota_observation_json, host_tests_json,
                       host_verified, host_tests_passed, result_accepted,
                       codex_correction_chars, recorded_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(task_id, request_id) DO UPDATE SET
                       metric_id=excluded.metric_id,
                       provider_id=excluded.provider_id,
                       provider_binding_id=excluded.provider_binding_id,
                       model_id=excluded.model_id,
                       intelligence_tier=excluded.intelligence_tier,
                       task_type=excluded.task_type,
                       elapsed_ms=excluded.elapsed_ms,
                       attempt_count=excluded.attempt_count,
                       retry_count=excluded.retry_count,
                       usage_json=excluded.usage_json,
                       quota_observation_json=excluded.quota_observation_json,
                       host_tests_json=excluded.host_tests_json,
                       host_verified=excluded.host_verified,
                       host_tests_passed=excluded.host_tests_passed,
                       result_accepted=excluded.result_accepted,
                       codex_correction_chars=excluded.codex_correction_chars,
                       recorded_at=excluded.recorded_at""",
                (
                    normalized["metric_id"],
                    normalized["task_id"],
                    normalized["request_id"],
                    normalized["provider_id"],
                    normalized["provider_binding_id"],
                    normalized["model_id"],
                    normalized["intelligence_tier"],
                    normalized["task_type"],
                    normalized["elapsed_ms"],
                    normalized["attempt_count"],
                    normalized["retry_count"],
                    json.dumps(normalized["usage"], ensure_ascii=False, sort_keys=True),
                    json.dumps(normalized["quota_observation"], ensure_ascii=False, sort_keys=True),
                    json.dumps(normalized["host_tests"], ensure_ascii=False, sort_keys=True),
                    1,
                    int(normalized["host_tests_passed"]),
                    int(normalized["result_accepted"]),
                    normalized["codex_correction_chars"],
                    normalized["recorded_at"],
                ),
            )
            self.connection.commit()
        return normalized

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = _integer(limit, "limit", minimum=1)
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM worker_metrics ORDER BY recorded_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def summarize(
        self,
        *,
        task_type: str | None = None,
        provider_binding_id: str | None = None,
        minimum_samples: int = 1,
    ) -> list[dict[str, Any]]:
        """Return measured groups; no group is eligible for routing by itself."""

        minimum_samples = _integer(minimum_samples, "minimum_samples", minimum=1)
        clauses: list[str] = []
        params: list[Any] = []
        if task_type is not None:
            clauses.append("task_type=?")
            params.append(_text(task_type, "task_type", max_length=64))
        if provider_binding_id is not None:
            clauses.append("provider_binding_id=?")
            params.append(_text(provider_binding_id, "provider_binding_id"))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        query = (
            "SELECT task_type, provider_id, provider_binding_id, model_id, "
            "intelligence_tier, COUNT(*) AS sample_count, "
            "SUM(result_accepted) AS accepted_count, "
            "AVG(elapsed_ms) AS average_elapsed_ms, "
            "AVG(retry_count) AS average_retry_count "
            "FROM worker_metrics" + where +
            " GROUP BY task_type, provider_id, provider_binding_id, model_id, intelligence_tier "
            "HAVING COUNT(*) >= ? ORDER BY task_type, provider_binding_id, model_id"
        )
        params.append(minimum_samples)
        with self._lock:
            rows = self.connection.execute(query, tuple(params)).fetchall()
        return [
            {
                "task_type": row["task_type"],
                "provider_id": row["provider_id"],
                "provider_binding_id": row["provider_binding_id"],
                "model_id": row["model_id"],
                "intelligence_tier": row["intelligence_tier"],
                "sample_count": int(row["sample_count"]),
                "accepted_count": int(row["accepted_count"]),
                "acceptance_rate": float(row["accepted_count"]) / float(row["sample_count"]),
                "average_elapsed_ms": float(row["average_elapsed_ms"]),
                "average_retry_count": float(row["average_retry_count"]),
            }
            for row in rows
        ]

    @staticmethod
    def _from_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["usage"] = json.loads(result.pop("usage_json"))
        result["quota_observation"] = json.loads(result.pop("quota_observation_json"))
        result["host_tests"] = json.loads(result.pop("host_tests_json"))
        for field in ("host_verified", "host_tests_passed", "result_accepted"):
            result[field] = bool(result[field])
        result["host_test_count"] = len(result["host_tests"])
        return result

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def __enter__(self) -> "WorkerMetricsStore":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = ["WorkerMetricsError", "WorkerMetricsStore"]
