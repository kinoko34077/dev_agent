"""Durable local admission for quota-telemetry-unknown domains."""

from __future__ import annotations

from dataclasses import dataclass
import math
import sqlite3
from threading import RLock
import time


@dataclass(frozen=True)
class UnknownQuotaAdmission:
    """Result of one bounded local admission decision.

    This is local policy state, not provider quota telemetry.  It is keyed by
    quota domain so multiple credentials cannot multiply an unknown allowance.
    """

    admitted: bool
    retry_at_epoch: float | None = None

    def __post_init__(self) -> None:
        if type(self.admitted) is not bool:
            raise ValueError("admitted must be a boolean")
        if self.retry_at_epoch is not None:
            if (
                isinstance(self.retry_at_epoch, bool)
                or not isinstance(self.retry_at_epoch, (int, float))
                or not math.isfinite(float(self.retry_at_epoch))
            ):
                raise ValueError("retry_at_epoch must be finite or None")


class QuotaAdmissionStore:
    """Persist and atomically update local unknown-quota admission windows."""

    def __init__(self, connection: sqlite3.Connection, lock: RLock) -> None:
        self.connection = connection
        self._lock = lock

    @staticmethod
    def _domain(quota_domain: str) -> str:
        if not isinstance(quota_domain, str) or not quota_domain.strip():
            raise ValueError("quota_domain must be a non-empty string")
        return quota_domain.strip()

    @staticmethod
    def _now(now_epoch: float | None) -> float:
        current = time.time() if now_epoch is None else now_epoch
        if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(float(current)):
            raise ValueError("now_epoch must be finite")
        return float(current)

    @staticmethod
    def _window(window_seconds: float) -> float:
        if (
            isinstance(window_seconds, bool)
            or not isinstance(window_seconds, (int, float))
            or not math.isfinite(float(window_seconds))
            or window_seconds <= 0
        ):
            raise ValueError("window_seconds must be a finite positive number")
        return float(window_seconds)

    @staticmethod
    def _limit(limit: int) -> int:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        return limit

    def claim(
        self,
        quota_domain: str,
        *,
        now_epoch: float | None,
        limit: int,
        window_seconds: float,
    ) -> UnknownQuotaAdmission:
        """Atomically claim one bounded local admission slot."""

        quota_domain = self._domain(quota_domain)
        current = self._now(now_epoch)
        limit = self._limit(limit)
        window_seconds = self._window(window_seconds)
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                row = self.connection.execute(
                    "SELECT window_started_at, admitted_count FROM quota_unknown_admissions WHERE quota_domain=?",
                    (quota_domain,),
                ).fetchone()
                if row is None or current >= float(row["window_started_at"]) + window_seconds:
                    self.connection.execute(
                        "INSERT INTO quota_unknown_admissions(quota_domain, window_started_at, admitted_count) VALUES (?, ?, 1) ON CONFLICT(quota_domain) DO UPDATE SET window_started_at=excluded.window_started_at, admitted_count=excluded.admitted_count",
                        (quota_domain, current),
                    )
                    self.connection.commit()
                    return UnknownQuotaAdmission(True)
                admitted_count = int(row["admitted_count"])
                retry_at = float(row["window_started_at"]) + window_seconds
                if admitted_count >= limit:
                    self.connection.commit()
                    return UnknownQuotaAdmission(False, retry_at)
                self.connection.execute(
                    "UPDATE quota_unknown_admissions SET admitted_count=admitted_count+1 WHERE quota_domain=?",
                    (quota_domain,),
                )
                self.connection.commit()
                return UnknownQuotaAdmission(True)
            except BaseException:
                self.connection.rollback()
                raise

    def release(self, quota_domain: str) -> None:
        """Return a local slot when no external request crossed the boundary."""

        quota_domain = self._domain(quota_domain)
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                row = self.connection.execute(
                    "SELECT admitted_count FROM quota_unknown_admissions WHERE quota_domain=?",
                    (quota_domain,),
                ).fetchone()
                if row is not None:
                    if int(row["admitted_count"]) <= 1:
                        self.connection.execute("DELETE FROM quota_unknown_admissions WHERE quota_domain=?", (quota_domain,))
                    else:
                        self.connection.execute(
                            "UPDATE quota_unknown_admissions SET admitted_count=admitted_count-1 WHERE quota_domain=?",
                            (quota_domain,),
                        )
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def due_domains(self, *, now_epoch: float | None, window_seconds: float) -> tuple[str, ...]:
        """List admission windows whose local cooldown has elapsed."""

        current = self._now(now_epoch)
        window_seconds = self._window(window_seconds)
        with self._lock:
            rows = self.connection.execute(
                "SELECT quota_domain, window_started_at FROM quota_unknown_admissions WHERE admitted_count > 0 ORDER BY quota_domain",
            ).fetchall()
        return tuple(
            str(row["quota_domain"])
            for row in rows
            if current >= float(row["window_started_at"]) + window_seconds
        )


__all__ = ["QuotaAdmissionStore", "UnknownQuotaAdmission"]
