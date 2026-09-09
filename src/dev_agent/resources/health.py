"""Durable provider health updates for the resource control plane."""

from __future__ import annotations

import sqlite3
import time
from threading import RLock


class ProviderHealthStore:
    """Persist provider circuit-breaker counters on a shared ledger DB."""

    def __init__(self, connection: sqlite3.Connection, lock: RLock) -> None:
        self._connection = connection
        self._lock = lock

    def record_failure(self, provider_id: str, *, threshold: int = 3, cooldown_seconds: float = 60.0) -> None:
        if threshold <= 0 or cooldown_seconds < 0:
            raise ValueError("threshold and cooldown must be positive")
        with self._lock:
            rows = self._connection.execute(
                "SELECT resource_id, consecutive_failures FROM resources WHERE provider_id=?",
                (provider_id,),
            ).fetchall()
            for row in rows:
                failures = int(row["consecutive_failures"]) + 1
                opened = time.time() + cooldown_seconds if failures >= threshold else 0
                self._connection.execute(
                    "UPDATE resources SET consecutive_failures=?, circuit_open_until=? WHERE resource_id=?",
                    (failures, opened, row["resource_id"]),
                )
            self._connection.commit()

    def record_success(self, provider_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE resources SET consecutive_failures=0, circuit_open_until=0 WHERE provider_id=?",
                (provider_id,),
            )
            self._connection.commit()


__all__ = ["ProviderHealthStore"]
