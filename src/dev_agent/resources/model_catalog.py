"""Immutable, exact provider-model discovery evidence.

Discovery answers only whether a named Provider binding reported a model as
available at a point in time.  It deliberately does not decide intelligence,
capability, billing, privacy, or runtime eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping


class ModelCatalogError(ValueError):
    """A model-discovery snapshot is malformed or ambiguous."""


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


def _current(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ModelCatalogError("now must include a timezone")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class ModelCatalogEntry:
    """Exact discovery evidence for one Provider binding and model."""

    provider_id: str
    provider_binding_id: str
    model_id: str
    source: str
    observed_at: str
    expires_at: str

    def __post_init__(self) -> None:
        for name in (
            "provider_id",
            "provider_binding_id",
            "model_id",
            "source",
            "observed_at",
            "expires_at",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        observed = _timestamp(self.observed_at, "observed_at")
        expires = _timestamp(self.expires_at, "expires_at")
        if expires <= observed:
            raise ModelCatalogError("expires_at must be after observed_at")

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.provider_id, self.provider_binding_id, self.model_id)

    def is_current(self, *, now: datetime | None = None) -> bool:
        current = _current(now)
        return _timestamp(self.observed_at, "observed_at") <= current < _timestamp(self.expires_at, "expires_at")


@dataclass(frozen=True)
class ModelCatalog:
    """One validated discovery snapshot; no implicit aliases or refreshes."""

    _entries_by_identity: Mapping[tuple[str, str, str], ModelCatalogEntry]

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "ModelCatalog":
        if not isinstance(document, Mapping):
            raise ModelCatalogError("model catalog must be an object")
        if document.get("schema_version") != 1:
            raise ModelCatalogError("model catalog schema_version must be 1")
        raw_entries = document.get("entries")
        if not isinstance(raw_entries, list):
            raise ModelCatalogError("model catalog entries must be an array")
        indexed: dict[tuple[str, str, str], ModelCatalogEntry] = {}
        for raw in raw_entries:
            if not isinstance(raw, Mapping):
                raise ModelCatalogError("model catalog entries must be objects")
            try:
                entry = ModelCatalogEntry(**dict(raw))
            except TypeError as exc:
                raise ModelCatalogError(f"invalid model catalog entry: {exc}") from exc
            if entry.identity in indexed:
                raise ModelCatalogError(f"duplicate discovered model identity: {entry.identity!r}")
            indexed[entry.identity] = entry
        return cls(MappingProxyType(indexed))

    def lookup(
        self,
        provider_id: str,
        provider_binding_id: str,
        model_id: str,
        *,
        now: datetime | None = None,
    ) -> ModelCatalogEntry | None:
        identity = (_text(provider_id, "provider_id"), _text(provider_binding_id, "provider_binding_id"), _text(model_id, "model_id"))
        entry = self._entries_by_identity.get(identity)
        return entry if entry is not None and entry.is_current(now=now) else None

    def entries_for_binding(self, provider_id: str, provider_binding_id: str, *, now: datetime | None = None) -> tuple[ModelCatalogEntry, ...]:
        provider = _text(provider_id, "provider_id")
        binding = _text(provider_binding_id, "provider_binding_id")
        return tuple(
            entry
            for entry in self._entries_by_identity.values()
            if entry.provider_id == provider and entry.provider_binding_id == binding and entry.is_current(now=now)
        )

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": entry.provider_id,
                    "provider_binding_id": entry.provider_binding_id,
                    "model_id": entry.model_id,
                    "source": entry.source,
                    "observed_at": entry.observed_at,
                    "expires_at": entry.expires_at,
                }
                for entry in sorted(self._entries_by_identity.values(), key=lambda item: item.identity)
            ],
        }


@dataclass(frozen=True)
class ModelAlias:
    """An operator-reviewed exact mapping from a Provider ID to a model identity."""

    provider_id: str
    model_id: str
    canonical_model_id: str

    def __post_init__(self) -> None:
        for name in ("provider_id", "model_id", "canonical_model_id"):
            object.__setattr__(self, name, _text(getattr(self, name), name))

    @property
    def identity(self) -> tuple[str, str]:
        return (self.provider_id, self.model_id)


@dataclass(frozen=True)
class ModelAliasCatalog:
    """Exact aliases; discovery must never choose a canonical identity by name similarity."""

    _aliases_by_identity: Mapping[tuple[str, str], ModelAlias]

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "ModelAliasCatalog":
        if not isinstance(document, Mapping):
            raise ModelCatalogError("model alias catalog must be an object")
        if document.get("schema_version") != 1:
            raise ModelCatalogError("model alias catalog schema_version must be 1")
        raw_entries = document.get("entries")
        if not isinstance(raw_entries, list):
            raise ModelCatalogError("model alias entries must be an array")
        indexed: dict[tuple[str, str], ModelAlias] = {}
        for raw in raw_entries:
            if not isinstance(raw, Mapping):
                raise ModelCatalogError("model alias entries must be objects")
            try:
                alias = ModelAlias(**dict(raw))
            except TypeError as exc:
                raise ModelCatalogError(f"invalid model alias entry: {exc}") from exc
            if alias.identity in indexed:
                raise ModelCatalogError(f"duplicate model alias identity: {alias.identity!r}")
            indexed[alias.identity] = alias
        return cls(MappingProxyType(indexed))

    def lookup(self, provider_id: str, model_id: str) -> ModelAlias | None:
        return self._aliases_by_identity.get((_text(provider_id, "provider_id"), _text(model_id, "model_id")))

    def to_document(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "entries": [
                {
                    "provider_id": alias.provider_id,
                    "model_id": alias.model_id,
                    "canonical_model_id": alias.canonical_model_id,
                }
                for alias in sorted(self._aliases_by_identity.values(), key=lambda item: item.identity)
            ],
        }


__all__ = ["ModelAlias", "ModelAliasCatalog", "ModelCatalog", "ModelCatalogEntry", "ModelCatalogError"]
