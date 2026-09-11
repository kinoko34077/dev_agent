"""Read-only projection of observed Provider qualification evidence.

The capability matrix is an evidence record, not a routing vocabulary.  This
module translates an exact, current provider/binding/model entry into the
small canonical capability set that ResourceRouter may consume.  It never
mutates the Resource catalog and never infers a qualification from a model
name alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from ..domain.capabilities import CANONICAL_EXECUTION_CAPABILITIES

CANONICAL_ROUTING_CAPABILITIES = CANONICAL_EXECUTION_CAPABILITIES
_VALID_CONFIDENCE = frozenset({"low", "medium", "high"})
ROUTING_MIN_CONFIDENCE = "high"
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
_INTEGRATION_EVIDENCE = frozenset(
    {"controller_e2e", "thought_signature_roundtrip", "durable_provider_audit", "budget_reconciliation"}
)
_TOOL_CALL_EVIDENCE = frozenset({"model_generated_tool_call", "tool_result_roundtrip", "final_response"})


class QualificationError(ValueError):
    """The protected qualification evidence is malformed."""


@dataclass(frozen=True)
class QualificationProjection:
    """Current qualification facts exposed to routing callers."""

    provider_id: str
    provider_binding_id: str
    model_id: str
    routing_capabilities: frozenset[str]
    qualification_evidence: frozenset[str]
    integration_evidence: frozenset[str]
    intelligence_tier: str | None
    tested_at: str
    expires_at: str
    confidence: str

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (self.provider_id, self.provider_binding_id, self.model_id, self.tested_at, self.expires_at, self.confidence)):
            raise ValueError("qualification identity and evidence timestamps are required")
        if self.intelligence_tier is not None and self.intelligence_tier not in {"L0", "L1", "L2", "L3"}:
            raise ValueError("intelligence_tier must be one of L0, L1, L2, or L3")
        if not self.routing_capabilities:
            raise ValueError("qualification must expose at least one routing capability")
        if not self.routing_capabilities <= CANONICAL_ROUTING_CAPABILITIES:
            raise ValueError("qualification contains a non-canonical routing capability")


def _parse_timestamp(value: Any, *, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise QualificationError(f"{name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise QualificationError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _entry_key(entry: Mapping[str, Any]) -> tuple[str, str, str]:
    values = (entry.get("provider"), entry.get("provider_binding_id"), entry.get("model"))
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise QualificationError("qualification entry identity is incomplete")
    provider, binding, model = (value.strip() for value in values)
    return provider, binding, model


def _identity(provider_id: str, provider_binding_id: str, model_id: str) -> tuple[str, str, str]:
    values = (provider_id, provider_binding_id, model_id)
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError("provider, binding, and model identity are required")
    return tuple(value.strip() for value in values)


def _validate_entry(entry: Mapping[str, Any]) -> tuple[str, str, str]:
    identity = _entry_key(entry)
    _parse_timestamp(entry.get("tested_at"), name="tested_at")
    _parse_timestamp(entry.get("expires_at"), name="expires_at")
    raw_values = entry.get("capabilities")
    if not isinstance(raw_values, list) or not all(isinstance(value, str) and value.strip() for value in raw_values):
        raise QualificationError("qualification capabilities must be a non-empty string array")
    tier = entry.get("intelligence_tier")
    if tier is not None and tier not in {"L0", "L1", "L2", "L3"}:
        raise QualificationError("qualification intelligence_tier is invalid")
    confidence = entry.get("confidence")
    if not isinstance(confidence, str) or confidence.strip().lower() not in _VALID_CONFIDENCE:
        raise QualificationError("qualification confidence is invalid")
    return identity


@dataclass(frozen=True)
class QualificationCatalog:
    """Immutable, indexed qualification evidence for one runtime session."""

    _entries_by_identity: Mapping[tuple[str, str, str], Mapping[str, Any]]

    @classmethod
    def from_entries(cls, entries: Iterable[Mapping[str, Any]]) -> "QualificationCatalog":
        if not isinstance(entries, Iterable) or isinstance(entries, (str, bytes, Mapping)):
            raise QualificationError("capability matrix entries must be an array")
        indexed: dict[tuple[str, str, str], Mapping[str, Any]] = {}
        for raw_entry in entries:
            if not isinstance(raw_entry, Mapping):
                raise QualificationError("qualification entries must be objects")
            entry = dict(raw_entry)
            identity = _validate_entry(entry)
            if identity in indexed:
                raise QualificationError(f"duplicate qualification identity: {identity!r}")
            indexed[identity] = MappingProxyType(entry)
        return cls(MappingProxyType(indexed))

    @classmethod
    def load(cls, path: str | Path | None = None) -> "QualificationCatalog":
        matrix_path = Path(path) if path is not None else QualificationResolver.DEFAULT_MATRIX_PATH
        try:
            document = json.loads(matrix_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QualificationError(f"unable to read capability matrix: {matrix_path}") from exc
        entries = document.get("entries") if isinstance(document, dict) else None
        return cls.from_entries(entries)

    def lookup(self, provider_id: str, provider_binding_id: str, model_id: str) -> Mapping[str, Any] | None:
        return self._entries_by_identity.get(_identity(provider_id, provider_binding_id, model_id))

    def identities_for(self, provider_id: str, model_id: str) -> tuple[tuple[str, str, str], ...]:
        """Return exact identities for a provider/model pair.

        Callers that do not know the binding must resolve this set before
        choosing one; silently taking the first binding would make activation
        order part of the security decision.
        """
        provider = provider_id.strip() if isinstance(provider_id, str) else ""
        model = model_id.strip() if isinstance(model_id, str) else ""
        return tuple(identity for identity in self._entries_by_identity if identity[0] == provider and identity[2] == model)


def _derive_capabilities(raw: frozenset[str]) -> frozenset[str]:
    capabilities: set[str] = set()
    if "text" in raw:
        capabilities.add("text")
    if _TOOL_CALL_EVIDENCE <= raw:
        capabilities.add("tool_call")
    for name in ("structured_output", "json", "long_context"):
        if name in raw:
            capabilities.add(name)
    return frozenset(capabilities)


class QualificationResolver:
    """Resolve exact current entries from the protected capability matrix."""

    DEFAULT_MATRIX_PATH = Path(__file__).resolve().parents[3] / "spec" / "v2" / "PROVIDER_CAPABILITY_MATRIX.json"

    def __init__(
        self,
        *,
        matrix_path: str | Path | None = None,
        entries: Iterable[Mapping[str, Any]] | None = None,
        catalog: QualificationCatalog | None = None,
    ) -> None:
        if catalog is not None and (matrix_path is not None or entries is not None):
            raise ValueError("catalog cannot be combined with matrix_path or entries")
        if catalog is None:
            catalog = QualificationCatalog.from_entries(entries) if entries is not None else QualificationCatalog.load(matrix_path)
        if not isinstance(catalog, QualificationCatalog):
            raise TypeError("catalog must be a QualificationCatalog")
        self._catalog = catalog

    @property
    def catalog(self) -> QualificationCatalog:
        return self._catalog

    def resolve(
        self,
        provider_id: str,
        provider_binding_id: str,
        model_id: str,
        *,
        now: datetime | None = None,
        min_confidence: str | None = ROUTING_MIN_CONFIDENCE,
    ) -> QualificationProjection | None:
        entry = self._catalog.lookup(provider_id, provider_binding_id, model_id)
        return None if entry is None else self._project(entry, now=now, min_confidence=min_confidence)

    def resolve_observed(
        self,
        provider_id: str,
        provider_binding_id: str,
        model_id: str,
        *,
        now: datetime | None = None,
    ) -> QualificationProjection | None:
        """Return current evidence without admitting it to Production routing."""

        return self.resolve(
            provider_id,
            provider_binding_id,
            model_id,
            now=now,
            min_confidence=None,
        )

    @staticmethod
    def _project(
        entry: Mapping[str, Any],
        *,
        now: datetime | None,
        min_confidence: str | None,
    ) -> QualificationProjection | None:
        tested = _parse_timestamp(entry.get("tested_at"), name="tested_at")
        expires = _parse_timestamp(entry.get("expires_at"), name="expires_at")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        if not tested <= current < expires:
            return None
        raw_values = entry.get("capabilities")
        if not isinstance(raw_values, list) or not all(isinstance(value, str) and value.strip() for value in raw_values):
            raise QualificationError("qualification capabilities must be a non-empty string array")
        raw = frozenset(value.strip() for value in raw_values)
        routing = _derive_capabilities(raw)
        if not routing:
            return None
        identity = _entry_key(entry)
        tier = entry.get("intelligence_tier")
        if tier is not None and tier not in {"L0", "L1", "L2", "L3"}:
            raise QualificationError("qualification intelligence_tier is invalid")
        confidence = entry.get("confidence")
        if not isinstance(confidence, str) or confidence.strip().lower() not in _VALID_CONFIDENCE:
            raise QualificationError("qualification confidence is invalid")
        normalized_confidence = confidence.strip().lower()
        if min_confidence is not None:
            if not isinstance(min_confidence, str) or min_confidence.strip().lower() not in _VALID_CONFIDENCE:
                raise QualificationError("minimum routing confidence is invalid")
            if _CONFIDENCE_RANK[normalized_confidence] < _CONFIDENCE_RANK[min_confidence.strip().lower()]:
                return None
        return QualificationProjection(
            provider_id=identity[0],
            provider_binding_id=identity[1],
            model_id=identity[2],
            routing_capabilities=routing,
            qualification_evidence=raw - _INTEGRATION_EVIDENCE,
            integration_evidence=raw & _INTEGRATION_EVIDENCE,
            intelligence_tier=tier,
            tested_at=entry["tested_at"].strip(),
            expires_at=entry["expires_at"].strip(),
            confidence=normalized_confidence,
        )


__all__ = [
    "CANONICAL_ROUTING_CAPABILITIES",
    "QualificationCatalog",
    "QualificationError",
    "QualificationProjection",
    "QualificationResolver",
    "ROUTING_MIN_CONFIDENCE",
]
