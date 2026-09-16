"""Immutable, exact provider-model discovery evidence.

Discovery answers only whether a named Provider binding reported a model as
available at a point in time.  It deliberately does not decide intelligence,
capability, billing, privacy, or runtime eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any, Mapping


class ModelCatalogError(ValueError):
    """A model-discovery snapshot is malformed or ambiguous."""


_MAX_METADATA_STRING = 256
_MAX_METADATA_ITEMS = 32
_MAX_METADATA_INT = 10_000_000_000
_ALLOWED_METADATA_KEYS = frozenset(
    {
        "display_name",
        "version",
        "base_model_id",
        "supported_generation_methods",
        "input_token_limit",
        "output_token_limit",
        "thinking_supported",
        "deprecated",
        "context_length",
        "modality",
        "input_modalities",
        "output_modalities",
        "supported_parameters",
    }
)


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


def _metadata(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ModelCatalogError("metadata must be an object")
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or key not in _ALLOWED_METADATA_KEYS:
            raise ModelCatalogError(f"metadata contains an unsupported field: {key!r}")
        if isinstance(item, str):
            item = item.strip()
            if not item or len(item) > _MAX_METADATA_STRING:
                raise ModelCatalogError(f"metadata.{key} must be a bounded string")
        elif isinstance(item, bool):
            pass
        elif isinstance(item, int):
            if item <= 0 or item > _MAX_METADATA_INT:
                raise ModelCatalogError(f"metadata.{key} must be a bounded positive integer")
        elif isinstance(item, (list, tuple)):
            if len(item) > _MAX_METADATA_ITEMS or not all(
                isinstance(entry, str) and entry.strip() and len(entry.strip()) <= _MAX_METADATA_STRING
                for entry in item
            ):
                raise ModelCatalogError(f"metadata.{key} must be a bounded string array")
            item = list(dict.fromkeys(entry.strip() for entry in item))
        else:
            raise ModelCatalogError(f"metadata.{key} must be JSON-safe")
        normalized[key] = item
    return MappingProxyType(normalized)


@dataclass(frozen=True)
class ModelCatalogEntry:
    """Exact discovery evidence for one Provider binding and model."""

    provider_id: str
    provider_binding_id: str
    model_id: str
    source: str
    observed_at: str
    expires_at: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

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
        object.__setattr__(self, "metadata", _metadata(self.metadata))

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.provider_id, self.provider_binding_id, self.model_id)

    def is_current(self, *, now: datetime | None = None) -> bool:
        current = _current(now)
        return _timestamp(self.observed_at, "observed_at") <= current < _timestamp(self.expires_at, "expires_at")

    def supports_generation_method(self, method: str) -> bool:
        """Return a metadata-backed method fact, or preserve legacy openness."""

        if not isinstance(method, str) or not method.strip():
            raise ValueError("method must be a non-empty string")
        methods = self.metadata.get("supported_generation_methods")
        return True if methods is None else method.strip() in methods

    def is_text_generation_candidate(self, *, min_input_token_limit: int = 0) -> bool:
        """Apply only conservative discovery metadata filters.

        An empty metadata object is a legacy snapshot and remains eligible for
        the downstream evidence layers.  Metadata never grants capabilities;
        it only excludes explicitly non-text, deprecated, or undersized rows.
        """

        if not isinstance(min_input_token_limit, int) or isinstance(min_input_token_limit, bool) or min_input_token_limit < 0:
            raise ValueError("min_input_token_limit must be a non-negative integer")
        if not self.metadata:
            return True
        if self.metadata.get("deprecated") is True or not self.supports_generation_method("generateContent"):
            return False
        if self.provider_id == "openrouter":
            for field_name in ("input_modalities", "output_modalities"):
                modalities = self.metadata.get(field_name)
                if isinstance(modalities, list) and "text" not in modalities:
                    return False
        input_limit = self.metadata.get("input_token_limit") or self.metadata.get("context_length")
        if isinstance(input_limit, int) and input_limit < min_input_token_limit:
            return False
        labels = " ".join(
            str(self.metadata.get(name, ""))
            for name in ("display_name", "base_model_id", "version")
        ).lower()
        model_label = f"{self.model_id.lower()} {labels}"
        specialized_markers = (
            "-tts",
            " tts",
            "-image",
            " image",
            "-live",
            " live",
            "-transcribe",
            " transcribe",
            "-robotics",
            " robotics",
            "-veo",
            " veo",
            "-imagen",
            " imagen",
            "deep-research",
            "computer-use",
            "customtools",
            "native-audio",
            "-audio",
            " audio",
            "omni",
            "lyria",
            "nano-banana",
            "antigravity",
        )
        return not any(marker in model_label for marker in specialized_markers)


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
            for entry in sorted(self._entries_by_identity.values(), key=lambda item: item.identity)
            if entry.provider_id == provider and entry.provider_binding_id == binding and entry.is_current(now=now)
        )

    def entries(self, *, now: datetime | None = None) -> tuple[ModelCatalogEntry, ...]:
        """Return current entries in deterministic exact-identity order."""

        return tuple(
            entry
            for entry in sorted(self._entries_by_identity.values(), key=lambda item: item.identity)
            if entry.is_current(now=now)
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
                    **({"metadata": dict(entry.metadata)} if entry.metadata else {}),
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
