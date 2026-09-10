"""SQLite repositories for current resource and quota observations."""

from __future__ import annotations

from collections.abc import Mapping
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid4


class ResourceObservationStore:
    """Persist current and historical operational resource observations."""

    def __init__(self, connection: sqlite3.Connection, lock: RLock) -> None:
        self.connection = connection
        self._lock = lock

    def record(
        self,
        *,
        resource_id: str,
        available: int | float,
        health: str,
        confidence: float,
        observed_at: str,
        quota_remaining_ratio: float | None,
        quota_reset_at: str | None,
        latency_ewma_ms: int | float | None,
        failure_ewma: float | None,
        inflight: int | float,
        concurrency_limit: int | float | None,
    ) -> None:
        with self._lock:
            resource = self.connection.execute("SELECT capacity FROM resources WHERE resource_id = ?", (resource_id,)).fetchone()
            if resource is None:
                raise KeyError(resource_id)
            if available > resource[0]:
                raise ValueError(f"available capacity exceeds resource capacity: {resource_id}")
            self.connection.execute(
                """UPDATE resources
                   SET available=?, health=?, confidence=?, observed_at=?,
                       quota_remaining_ratio=?, quota_reset_at=?,
                       latency_ewma_ms=?, failure_ewma=?, inflight=?,
                       concurrency_limit=?
                   WHERE resource_id=?""",
                (
                    available,
                    health,
                    confidence,
                    observed_at,
                    quota_remaining_ratio,
                    quota_reset_at,
                    latency_ewma_ms,
                    failure_ewma,
                    inflight,
                    concurrency_limit,
                    resource_id,
                ),
            )
            self.connection.execute(
                """INSERT INTO resource_observations(
                    observation_id, resource_id, available, health, confidence,
                    observed_at, quota_remaining_ratio, quota_reset_at,
                    latency_ewma_ms, failure_ewma, inflight, concurrency_limit
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    resource_id,
                    available,
                    health,
                    confidence,
                    observed_at,
                    quota_remaining_ratio,
                    quota_reset_at,
                    latency_ewma_ms,
                    failure_ewma,
                    inflight,
                    concurrency_limit,
                ),
            )
            self.connection.commit()


class QuotaObservationStore:
    """Persist provider-reported or estimated quota observations."""

    _SELECT = """SELECT resource_id, quota_domain, unit, limit_value,
                          remaining_value, consumed_value, authority,
                          metric, window, reset_source, blocked_until,
                          block_reason,
                          request_limit,
                          request_remaining, token_limit, token_remaining,
                          reset_at, daily_remaining, concurrency_limit,
                          confidence, observed_at, source
                   FROM quota_observations"""

    def __init__(self, connection: sqlite3.Connection, lock: RLock) -> None:
        self.connection = connection
        self._lock = lock

    def record(
        self,
        *,
        resource_id: str,
        quota_domain: str,
        unit: str,
        limit: int | float | None,
        remaining: int | float | None,
        consumed: int | float | None,
        authority: str,
        metric: str,
        window: str,
        reset_source: str | None,
        blocked_until: str | None,
        block_reason: str | None,
        request_limit: int | None,
        request_remaining: int | None,
        token_limit: int | None,
        token_remaining: int | None,
        reset_at: str | None,
        daily_remaining: int | None,
        concurrency_limit: int | float | None,
        confidence: float,
        observed_at: str,
        source: str,
    ) -> None:
        with self._lock:
            resource = self.connection.execute("SELECT quota_domain FROM resources WHERE resource_id = ?", (resource_id,)).fetchone()
            if resource is None:
                raise KeyError(resource_id)
            if resource["quota_domain"] != quota_domain:
                raise ValueError("quota_domain does not match resource")
            self.connection.execute(
                """INSERT INTO quota_observations(
                    observation_id, resource_id, quota_domain, unit, limit_value,
                    remaining_value, consumed_value, authority, metric, window,
                    reset_source, blocked_until, block_reason, request_limit,
                    request_remaining, token_limit, token_remaining, reset_at,
                    daily_remaining, concurrency_limit, confidence, observed_at,
                    source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    resource_id,
                    quota_domain,
                    unit,
                    limit,
                    remaining,
                    consumed,
                    authority,
                    metric,
                    window,
                    reset_source,
                    blocked_until,
                    block_reason,
                    request_limit,
                    request_remaining,
                    token_limit,
                    token_remaining,
                    reset_at,
                    daily_remaining,
                    concurrency_limit,
                    confidence,
                    observed_at,
                    source,
                ),
            )
            self.connection.commit()

    @staticmethod
    def from_row(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["limit"] = result.pop("limit_value")
        result["remaining"] = result.pop("remaining_value")
        result["consumed"] = result.pop("consumed_value")
        return result

    def get_latest(self, resource_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.connection.execute(
                self._SELECT + " WHERE resource_id=? ORDER BY observed_at DESC, rowid DESC LIMIT 1",
                (resource_id,),
            ).fetchone()
            return self.from_row(row) if row is not None else None

    def list_latest(self, *, quota_domain: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            clauses = " WHERE quota_domain=?" if quota_domain is not None else ""
            params: tuple[object, ...] = (quota_domain,) if quota_domain is not None else ()
            rows = self.connection.execute(self._SELECT + clauses + " ORDER BY observed_at DESC, rowid DESC", params).fetchall()
            latest: dict[str, dict[str, Any]] = {}
            for row in rows:
                latest.setdefault(row["resource_id"], self.from_row(row))
            return [latest[resource_id] for resource_id in sorted(latest)]

    def rows_for_domains(self, domains: list[str]) -> list[sqlite3.Row]:
        if not domains:
            return []
        placeholders = ",".join("?" for _ in domains)
        with self._lock:
            return self.connection.execute(
                """SELECT q.resource_id, q.quota_domain, q.unit, q.limit_value,
                          q.remaining_value, q.consumed_value, q.authority,
                          q.metric, q.window, q.reset_source, q.blocked_until,
                          q.block_reason,
                          q.request_limit, q.request_remaining, q.token_limit,
                          q.token_remaining, q.reset_at, q.daily_remaining,
                          q.concurrency_limit, q.confidence, q.observed_at,
                          q.source
                   FROM quota_observations AS q
                   JOIN resources AS r
                     ON r.resource_id = q.resource_id
                    AND r.quota_domain = q.quota_domain
                   WHERE q.quota_domain IN (""" + placeholders + ") ORDER BY q.observed_at DESC, q.rowid DESC",
                tuple(domains),
            ).fetchall()


__all__ = ["QuotaObservationStore", "ResourceObservationStore"]
