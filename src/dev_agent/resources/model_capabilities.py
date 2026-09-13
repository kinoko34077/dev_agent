"""Provider-model capability facts kept separate from performance evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping

from ..domain.capabilities import CANONICAL_EXECUTION_CAPABILITIES
from .model_catalog import ModelCatalogError


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelCatalogError(f"{name} must be a non-empty string")
    return value.strip()


def _timestamp(value: Any, name: str) -> datetime:
    text = _text(value, name)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ModelCatalogError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ModelCatalogError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class ModelCapabilityEntry:
    provider_id: str
    model_id: str
    capabilities: frozenset[str]
    source: str
    observed_at: str
    expires_at: str

    def __post_init__(self) -> None:
        for name in ("provider_id", "model_id", "source", "observed_at", "expires_at"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        observed = _timestamp(self.observed_at, "observed_at")
        expires = _timestamp(self.expires_at, "expires_at")
        if expires <= observed:
            raise ModelCatalogError("expires_at must be after observed_at")
        if not isinstance(self.capabilities, (set, frozenset, list, tuple)):
            raise ModelCatalogError("capabilities must be a non-empty string array")
        capabilities = frozenset(_text(item, "capability") for item in self.capabilities)
        if not capabilities or not capabilities <= CANONICAL_EXECUTION_CAPABILITIES:
            raise ModelCatalogError("capabilities must use the canonical execution vocabulary")
        object.__setattr__(self, "capabilities", capabilities)

    @property
    def identity(self) -> tuple[str, str]:
        return (self.provider_id, self.model_id)

    def is_current(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ModelCatalogError("now must include a timezone")
        current = current.astimezone(timezone.utc)
        return _timestamp(self.observed_at, "observed_at") <= current < _timestamp(self.expires_at, "expires_at")


@dataclass(frozen=True)
class ModelCapabilityCatalog:
    _entries_by_identity: Mapping[tuple[str, str], ModelCapabilityEntry]

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "ModelCapabilityCatalog":
        if not isinstance(document, Mapping):
            raise ModelCatalogError("capability catalog must be an object")
        if document.get("schema_version") != 1:
            raise ModelCatalogError("capability catalog schema_version must be 1")
        raw_entries = document.get("entries")
        if not isinstance(raw_entries, list):
            raise ModelCatalogError("capability catalog entries must be an array")
        indexed: dict[tuple[str, str], ModelCapabilityEntry] = {}
        for raw in raw_entries:
            if not isinstance(raw, Mapping):
                raise ModelCatalogError("capability entries must be objects")
            try:
                entry = ModelCapabilityEntry(**dict(raw))
            except TypeError as exc:
                raise ModelCatalogError(f"invalid capability entry: {exc}") from exc
            if entry.identity in indexed:
                raise ModelCatalogError(f"duplicate capability identity: {entry.identity!r}")
            indexed[entry.identity] = entry
        return cls(MappingProxyType(indexed))

    def lookup(self, provider_id: str, model_id: str, *, now: datetime | None = None) -> ModelCapabilityEntry | None:
        entry = self._entries_by_identity.get((_text(provider_id, "provider_id"), _text(model_id, "model_id")))
        return entry if entry is not None and entry.is_current(now=now) else None

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": entry.provider_id,
                    "model_id": entry.model_id,
                    "capabilities": sorted(entry.capabilities),
                    "source": entry.source,
                    "observed_at": entry.observed_at,
                    "expires_at": entry.expires_at,
                }
                for entry in sorted(self._entries_by_identity.values(), key=lambda item: item.identity)
            ],
        }


__all__ = ["ModelCapabilityCatalog", "ModelCapabilityEntry"]
