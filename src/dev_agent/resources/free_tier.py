"""Durable conservative accounting for explicitly no-charge quotas.

This store lives on the existing :class:`ResourceLedger` SQLite connection.
It is deliberately narrower than the provider-observation history: an entry
represents one provider/binding/model/domain and one documented allowance
period.  It never manufactures provider-authoritative remaining values.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import math
import sqlite3
from threading import RLock
from typing import Any


EVIDENCE_MODE = "derived_conservative"
_UNITS = {"neurons", "requests", "tokens"}


class FreeTierQuotaExhausted(RuntimeError):
    """The local conservative allowance cannot admit another request."""


def _timestamp(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _amount(value: int | float, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    if not math.isfinite(float(value)) or value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _ledger_key(provider_id: str, binding_id: str, model_id: str, quota_domain: str, period_id: str) -> str:
    identity = [provider_id, binding_id, model_id, quota_domain, period_id]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


class FreeTierQuotaStore:
    """Persist one bounded derived-conservative allowance period per route."""

    def __init__(self, connection: sqlite3.Connection, lock: RLock) -> None:
        self.connection = connection
        self._lock = lock

    @staticmethod
    def _validate_identity(*values: str) -> tuple[str, ...]:
        normalized = []
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("free-tier quota identity fields must be non-empty strings")
            normalized.append(value.strip())
        return tuple(normalized)

    @staticmethod
    def _row(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        result["exhausted"] = bool(result["exhausted"])
        result["remaining"] = max(0, result["allowance_limit"] - result["consumed"])
        result["admittable"] = not result["exhausted"] and result["consumed"] < result["allowance_limit"]
        return result

    def _get_by_key(self, key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM free_tier_quota_ledgers WHERE ledger_key=?",
            (key,),
        ).fetchone()
        return self._row(row) if row is not None else None

    def configure(
        self,
        *,
        provider_id: str,
        binding_id: str,
        model_id: str,
        quota_domain: str,
        unit: str,
        allowance_limit: int | float,
        period_id: str,
        reset_at: str,
        reset_source: str,
        observed_at: str,
        source: str,
    ) -> dict[str, Any]:
        provider_id, binding_id, model_id, quota_domain, period_id = self._validate_identity(
            provider_id, binding_id, model_id, quota_domain, period_id
        )
        if unit not in _UNITS:
            raise ValueError("unit must be one of neurons, requests, or tokens")
        allowance_limit = _amount(allowance_limit, "allowance_limit")
        if allowance_limit <= 0:
            raise ValueError("allowance_limit must be positive")
        reset_at = _timestamp(reset_at, "reset_at")
        observed_at = _timestamp(observed_at, "observed_at")
        reset_source, source = self._validate_identity(reset_source, source)
        key = _ledger_key(provider_id, binding_id, model_id, quota_domain, period_id)
        with self._lock:
            existing = self._get_by_key(key)
            if existing is None:
                self.connection.execute(
                    """INSERT INTO free_tier_quota_ledgers(
                           ledger_key, provider_id, binding_id, model_id,
                           quota_domain, unit, period_id, allowance_limit,
                           consumed, reset_at, reset_source, quota_authority,
                           evidence_mode, exhausted, blocked_until,
                           block_reason, observed_at, updated_at, source
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, 0, NULL, NULL, ?, ?, ?)""",
                    (
                        key, provider_id, binding_id, model_id, quota_domain,
                        unit, period_id, allowance_limit, reset_at, reset_source,
                        EVIDENCE_MODE, EVIDENCE_MODE, observed_at, observed_at, source,
                    ),
                )
            else:
                if existing["unit"] != unit or existing["allowance_limit"] != allowance_limit or existing["reset_at"] != reset_at:
                    raise ValueError("free-tier quota period configuration is immutable")
                self.connection.execute(
                    "UPDATE free_tier_quota_ledgers SET updated_at=?, observed_at=?, source=? WHERE ledger_key=?",
                    (observed_at, observed_at, source, key),
                )
            self.connection.commit()
            return self._get_by_key(key)  # type: ignore[return-value]

    def get(self, *, provider_id: str, binding_id: str, model_id: str, quota_domain: str, period_id: str) -> dict[str, Any] | None:
        identity = self._validate_identity(provider_id, binding_id, model_id, quota_domain, period_id)
        with self._lock:
            return self._get_by_key(_ledger_key(*identity))

    def latest_for_identity(self, *, provider_id: str, binding_id: str, model_id: str, quota_domain: str) -> dict[str, Any] | None:
        provider_id, binding_id, model_id, quota_domain = self._validate_identity(provider_id, binding_id, model_id, quota_domain)
        with self._lock:
            row = self.connection.execute(
                """SELECT * FROM free_tier_quota_ledgers
                   WHERE provider_id=? AND binding_id=? AND model_id=? AND quota_domain=?
                   ORDER BY updated_at DESC, rowid DESC LIMIT 1""",
                (provider_id, binding_id, model_id, quota_domain),
            ).fetchone()
            return self._row(row) if row is not None else None

    def debit(
        self,
        *,
        provider_id: str,
        binding_id: str,
        model_id: str,
        quota_domain: str,
        period_id: str,
        consumed: int | float,
        observed_at: str,
        source: str,
        debit_key: str | None = None,
    ) -> dict[str, Any]:
        consumed = _amount(consumed, "consumed")
        if consumed <= 0:
            raise ValueError("consumed must be positive")
        observed_at = _timestamp(observed_at, "observed_at")
        source = self._validate_identity(source)[0]
        identity = self._validate_identity(provider_id, binding_id, model_id, quota_domain, period_id)
        key = _ledger_key(*identity)
        with self._lock:
            current = self._get_by_key(key)
            if current is None:
                raise KeyError(key)
            if debit_key is not None:
                debit_key = self._validate_identity(debit_key)[0]
                prior = self.connection.execute(
                    "SELECT ledger_key FROM free_tier_quota_debits WHERE debit_key=?",
                    (debit_key,),
                ).fetchone()
                if prior is not None:
                    return current
            if current["exhausted"] or current["consumed"] + consumed > current["allowance_limit"]:
                self.connection.execute(
                    """UPDATE free_tier_quota_ledgers
                       SET exhausted=1, blocked_until=?, block_reason=?,
                           observed_at=?, updated_at=?, source=?
                       WHERE ledger_key=?""",
                    (
                        current["reset_at"], "local_conservative_limit", observed_at,
                        observed_at, source, key,
                    ),
                )
                self.connection.commit()
                raise FreeTierQuotaExhausted("free-tier conservative allowance exhausted")
            self.connection.execute(
                """UPDATE free_tier_quota_ledgers
                   SET consumed=consumed+?, observed_at=?, updated_at=?, source=?
                   WHERE ledger_key=?""",
                (consumed, observed_at, observed_at, source, key),
            )
            if debit_key is not None:
                self.connection.execute(
                    """INSERT INTO free_tier_quota_debits(
                           debit_key, ledger_key, consumed, observed_at, source
                       ) VALUES (?, ?, ?, ?, ?)""",
                    (debit_key, key, consumed, observed_at, source),
                )
            self.connection.commit()
            return self._get_by_key(key)  # type: ignore[return-value]

    def mark_exhausted(
        self,
        *,
        provider_id: str,
        binding_id: str,
        model_id: str,
        quota_domain: str,
        period_id: str,
        blocked_until: str,
        reason: str,
        observed_at: str,
        source: str,
    ) -> dict[str, Any]:
        blocked_until = _timestamp(blocked_until, "blocked_until")
        observed_at = _timestamp(observed_at, "observed_at")
        reason, source = self._validate_identity(reason, source)
        identity = self._validate_identity(provider_id, binding_id, model_id, quota_domain, period_id)
        key = _ledger_key(*identity)
        with self._lock:
            if self._get_by_key(key) is None:
                raise KeyError(key)
            self.connection.execute(
                """UPDATE free_tier_quota_ledgers
                   SET exhausted=1, blocked_until=?, block_reason=?,
                       observed_at=?, updated_at=?, source=?
                   WHERE ledger_key=?""",
                (blocked_until, reason, observed_at, observed_at, source, key),
            )
            self.connection.commit()
            return self._get_by_key(key)  # type: ignore[return-value]


__all__ = ["EVIDENCE_MODE", "FreeTierQuotaExhausted", "FreeTierQuotaStore"]
