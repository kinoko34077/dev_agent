"""Durable provider health updates for the resource control plane."""

from __future__ import annotations

import sqlite3
import time
import json
from threading import RLock


class ProviderHealthStore:
    """Persist provider circuit-breaker counters on a shared ledger DB."""

    def __init__(self, connection: sqlite3.Connection, lock: RLock) -> None:
        self._connection = connection
        self._lock = lock

    def _target_resource_ids(
        self,
        provider_id: str,
        *,
        resource_id: str | None = None,
        provider_binding_id: str | None = None,
    ) -> tuple[str, ...]:
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("provider_id must be a non-empty string")
        if resource_id is not None:
            row = self._connection.execute(
                "SELECT resource_id, provider_id FROM resources WHERE resource_id=?",
                (resource_id,),
            ).fetchone()
            if row is None:
                raise KeyError(resource_id)
            if row["provider_id"] != provider_id:
                raise ValueError("resource does not belong to provider")
            return (row["resource_id"],)
        rows = self._connection.execute(
            "SELECT resource_id, metadata_json FROM resources WHERE provider_id=?",
            (provider_id,),
        ).fetchall()
        if provider_binding_id is None:
            # Compatibility callers that explicitly operate on a vendor-wide
            # health view retain the old behavior.  Runtime dispatch always
            # supplies the selected resource/binding and therefore does not
            # fan out across sibling models.
            return tuple(row["resource_id"] for row in rows)
        binding = provider_binding_id.strip()
        if not binding:
            raise ValueError("provider_binding_id must be a non-empty string")
        matched: list[str] = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"])
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            candidate = metadata.get("provider_binding_id") if isinstance(metadata, dict) else None
            if candidate == binding:
                matched.append(row["resource_id"])
        return tuple(matched)

    def record_failure(
        self,
        provider_id: str,
        *,
        resource_id: str | None = None,
        provider_binding_id: str | None = None,
        threshold: int = 3,
        cooldown_seconds: float = 60.0,
    ) -> None:
        if threshold <= 0 or cooldown_seconds < 0:
            raise ValueError("threshold and cooldown must be positive")
        with self._lock:
            resource_ids = self._target_resource_ids(
                provider_id,
                resource_id=resource_id,
                provider_binding_id=provider_binding_id,
            )
            if not resource_ids:
                return
            placeholders = ",".join("?" for _ in resource_ids)
            rows = self._connection.execute(
                f"SELECT resource_id, consecutive_failures FROM resources WHERE resource_id IN ({placeholders})",
                resource_ids,
            ).fetchall()
            for row in rows:
                failures = int(row["consecutive_failures"]) + 1
                opened = time.time() + cooldown_seconds if failures >= threshold else 0
                self._connection.execute(
                    "UPDATE resources SET consecutive_failures=?, circuit_open_until=? WHERE resource_id=?",
                    (failures, opened, row["resource_id"]),
                )
            self._connection.commit()

    def record_success(
        self,
        provider_id: str,
        *,
        resource_id: str | None = None,
        provider_binding_id: str | None = None,
    ) -> None:
        with self._lock:
            resource_ids = self._target_resource_ids(
                provider_id,
                resource_id=resource_id,
                provider_binding_id=provider_binding_id,
            )
            if not resource_ids:
                return
            placeholders = ",".join("?" for _ in resource_ids)
            self._connection.execute(
                f"UPDATE resources SET consecutive_failures=0, circuit_open_until=0 WHERE resource_id IN ({placeholders})",
                resource_ids,
            )
            self._connection.commit()


__all__ = ["ProviderHealthStore"]
