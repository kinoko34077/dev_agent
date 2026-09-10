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
from typing import Any, Iterable, Mapping


CANONICAL_ROUTING_CAPABILITIES = frozenset(
    {"text", "tool_call", "structured_output", "json", "long_context"}
)
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


def _derive_capabilities(raw: frozenset[str]) -> frozenset[str]:
    capabilities: set[str] = set()
    if "text" in raw:
        capabilities.add("text")
    if _TOOL_CALL_EVIDENCE <= raw or "tool_call" in raw:
        capabilities.add("tool_call")
    for name in ("structured_output", "json", "long_context"):
        if name in raw:
            capabilities.add(name)
    return frozenset(capabilities)


class QualificationResolver:
    """Resolve exact current entries from the protected capability matrix."""

    DEFAULT_MATRIX_PATH = Path(__file__).resolve().parents[3] / "spec" / "v2" / "PROVIDER_CAPABILITY_MATRIX.json"

    def __init__(self, *, matrix_path: str | Path | None = None, entries: Iterable[Mapping[str, Any]] | None = None) -> None:
        if entries is None:
            path = Path(matrix_path) if matrix_path is not None else self.DEFAULT_MATRIX_PATH
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise QualificationError(f"unable to read capability matrix: {path}") from exc
            entries = document.get("entries") if isinstance(document, dict) else None
        if not isinstance(entries, Iterable) or isinstance(entries, (str, bytes, Mapping)):
            raise QualificationError("capability matrix entries must be an array")
        self._entries = tuple(dict(entry) for entry in entries if isinstance(entry, Mapping))

    def resolve(
        self,
        provider_id: str,
        provider_binding_id: str,
        model_id: str,
        *,
        now: datetime | None = None,
    ) -> QualificationProjection | None:
        identity = (provider_id.strip(), provider_binding_id.strip(), model_id.strip())
        if not all(identity):
            raise ValueError("provider, binding, and model identity are required")
        for entry in self._entries:
            if _entry_key(entry) != identity:
                continue
            projection = self._project(entry, now=now)
            return projection
        return None

    @staticmethod
    def _project(entry: Mapping[str, Any], *, now: datetime | None) -> QualificationProjection | None:
        tested = _parse_timestamp(entry.get("tested_at"), name="tested_at")
        expires = _parse_timestamp(entry.get("expires_at"), name="expires_at")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        if expires <= tested or not tested <= current < expires:
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
        if not isinstance(confidence, str) or not confidence.strip():
            raise QualificationError("qualification confidence is required")
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
            confidence=confidence.strip(),
        )


__all__ = [
    "CANONICAL_ROUTING_CAPABILITIES",
    "QualificationError",
    "QualificationProjection",
    "QualificationResolver",
]
