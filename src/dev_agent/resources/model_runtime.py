MethodException: 
Line |
   2 |  … runtime.py"); $c=$c.Replace(([char]13)+([char]10),([char]10)); [Conso …
     |                  ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
     | Cannot convert argument "oldChar", with value: "
", for "Replace" to type "System.Char": "Cannot convert value "
" to type "System.Char". Error: "String must be exactly one character long.""
"""Bounded runtime-admission observations for model diagnostics.

This module is an evidence projection, not a second Provider router.  The
existing Resource/Provider authority may emit one observation for an exact
``(provider, binding, model)`` identity after a bounded read-only health or
quota check.  The diagnostic CLI consumes that projection without performing
generation or changing routing state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping


RUNTIME_ELIGIBLE = "RUNTIME_ELIGIBLE"
RUNTIME_UNAVAILABLE = "RUNTIME_UNAVAILABLE"
RUNTIME_UNKNOWN = "RUNTIME_UNKNOWN"
RUNTIME_NOT_PROBED = "RUNTIME_NOT_PROBED"
_OBSERVED_STATUSES = frozenset({RUNTIME_ELIGIBLE, RUNTIME_UNAVAILABLE, RUNTIME_UNKNOWN})
_MAX_SOURCE_LENGTH = 128


def _text(value: Any, name: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > max_length:
        raise ValueError(f"{name} must be a bounded non-empty string")
    return value.strip()


def _timestamp(value: Any, name: str) -> str:
    text = _text(value, name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class RuntimeAdmissionObservation:
    """One bounded observation owned by an existing runtime authority."""

    provider_id: str
    provider_binding_id: str
    model_id: str
    status: str
    observed_at: str
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _text(self.provider_id, "provider_id"))
        object.__setattr__(self, "provider_binding_id", _text(self.provider_binding_id, "provider_binding_id"))
        object.__setattr__(self, "model_id", _text(self.model_id, "model_id"))
        status = _text(self.status, "status", max_length=64).upper()
        if status not in _OBSERVED_STATUSES:
            raise ValueError(f"status must be one of {sorted(_OBSERVED_STATUSES)}")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "observed_at", _timestamp(self.observed_at, "observed_at"))
        object.__setattr__(self, "source", _text(self.source, "source", max_length=_MAX_SOURCE_LENGTH))

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.provider_id, self.provider_binding_id, self.model_id)

    def to_dict(self) -> dict[str, str]:
        return {
            "provider_id": self.provider_id,
            "provider_binding_id": self.provider_binding_id,
            "model_id": self.model_id,
            "status": self.status,
            "observed_at": self.observed_at,
            "source": self.source,
        }


@dataclass(frozen=True)
class RuntimeAdmissionSnapshot:
    """Immutable exact-identity runtime observations for one diagnostic run."""

    _observations: Mapping[tuple[str, str, str], RuntimeAdmissionObservation]

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "RuntimeAdmissionSnapshot":
        if not isinstance(document, Mapping) or document.get("schema_version") != 1:
            raise ValueError("runtime admission snapshot schema_version must be 1")
        raw_entries = document.get("observations")
        if not isinstance(raw_entries, list):
            raise ValueError("runtime admission snapshot observations must be an array")
        observations: dict[tuple[str, str, str], RuntimeAdmissionObservation] = {}
        for raw in raw_entries:
            if not isinstance(raw, Mapping):
                raise ValueError("runtime admission observations must be objects")
            try:
                observation = RuntimeAdmissionObservation(**dict(raw))
            except TypeError as exc:
                raise ValueError(f"invalid runtime admission observation: {exc}") from exc
            if observation.identity in observations:
                raise ValueError(f"duplicate runtime admission identity: {observation.identity!r}")
            observations[observation.identity] = observation
        return cls(MappingProxyType(observations))

    @classmethod
    def empty(cls) -> "RuntimeAdmissionSnapshot":
        return cls(MappingProxyType({}))

    def lookup(self, provider_id: str, provider_binding_id: str, model_id: str) -> RuntimeAdmissionObservation | None:
        return self._observations.get((provider_id, provider_binding_id, model_id))

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "observations": [
                observation.to_dict()
                for observation in sorted(self._observations.values(), key=lambda item: item.identity)
            ],
        }


__all__ = [
    "RUNTIME_ELIGIBLE",
    "RUNTIME_UNAVAILABLE",
    "RUNTIME_UNKNOWN",
    "RUNTIME_NOT_PROBED",
    "RuntimeAdmissionObservation",
    "RuntimeAdmissionSnapshot",
]
