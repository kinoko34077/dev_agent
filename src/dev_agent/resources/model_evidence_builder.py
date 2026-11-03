"""Deterministic builders for operator-reviewed model evidence snapshots.

Discovery remains factual and non-authoritative.  These helpers only turn
current, bounded discovery metadata into alias/capability *candidates*; the
result still needs the normal reviewed snapshot and exact qualification,
billing, privacy, quota, and health gates before dispatch.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .model_catalog import ModelCatalog, ModelCatalogEntry, ModelCatalogError


# Exact cross-provider/variant identities observed in the external benchmark
# source. These aliases are used only to look up quality scores; capability
# evidence remains keyed by the exact Provider/model ID. Do not generalize by
# stripping suffixes or comparing similar names.
_BENCHMARK_IDENTITY_OVERRIDES = {
    ("mistral", "mistral-large-4-0"): "mistralai/mistral-large-4-0",
    ("mistral", "mistral-small-2603"): "mistralai/mistral-small-2603",
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"): "nvidia/nemotron-3-ultra-550b-a55b",
    ("openrouter", "nvidia/nemotron-3.5-lightning:free"): "nvidia/nemotron-3.5-lightning",
    ("openrouter", "nvidia/nemotron-3.5-super-vl:free"): "nvidia/nemotron-3.5-super-vl",
    ("openrouter", "thinkingmachines/inkling-small:free"): "thinkingmachines/inkling-small",
    ("openrouter", "thinkingmachines/inkling:free"): "thinkingmachines/inkling",
}


def canonical_model_id_for(provider_id: str, model_id: str) -> str:
    """Return an exact reviewed benchmark identity, never a fuzzy alias."""

    if not isinstance(provider_id, str) or not provider_id.strip():
        raise ModelCatalogError("provider_id must be a non-empty string")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ModelCatalogError("model_id must be a non-empty string")
    provider = provider_id.strip().lower()
    model = model_id.strip()
    override = _BENCHMARK_IDENTITY_OVERRIDES.get((provider, model))
    if override is not None:
        return override
    if provider == "gemini":
        return f"google/{model}"
    if provider in {"openrouter", "vercel"} and "/" in model:
        return model
    return f"{provider}/{model}"


def _current_entries(catalog: ModelCatalog, now: datetime | None) -> tuple[ModelCatalogEntry, ...]:
    return tuple(entry for entry in catalog.entries(now=now) if entry.is_text_generation_candidate())


def build_alias_document(catalog: ModelCatalog, *, now: datetime | None = None) -> dict[str, Any]:
    """Build exact aliases for current ordinary text-generation entries."""

    if not isinstance(catalog, ModelCatalog):
        raise TypeError("catalog must be a ModelCatalog")
    aliases: dict[tuple[str, str], str] = {}
    for entry in _current_entries(catalog, now):
        identity = (entry.provider_id, entry.model_id)
        canonical = canonical_model_id_for(*identity)
        previous = aliases.get(identity)
        if previous is not None and previous != canonical:
            raise ModelCatalogError(f"conflicting deterministic alias: {identity!r}")
        aliases[identity] = canonical
    return {
        "schema_version": 1,
        "entries": [
            {
                "provider_id": provider_id,
                "model_id": model_id,
                "canonical_model_id": canonical,
            }
            for (provider_id, model_id), canonical in sorted(aliases.items())
        ],
    }


def derive_capabilities(entry: ModelCatalogEntry, *, min_long_context_tokens: int = 32_768) -> frozenset[str]:
    """Derive only facts the model-list metadata can safely establish."""

    if not isinstance(entry, ModelCatalogEntry):
        raise TypeError("entry must be a ModelCatalogEntry")
    if isinstance(min_long_context_tokens, bool) or not isinstance(min_long_context_tokens, int) or min_long_context_tokens < 0:
        raise ValueError("min_long_context_tokens must be a non-negative integer")
    if not entry.metadata or not entry.supports_generation_method("generateContent"):
        return frozenset()
    if entry.provider_id == "mistral" and entry.metadata.get("chat_completion_supported") is not True:
        return frozenset()
    if entry.provider_id == "openrouter":
        input_modalities = entry.metadata.get("input_modalities")
        output_modalities = entry.metadata.get("output_modalities")
        if (
            not isinstance(input_modalities, list)
            or not isinstance(output_modalities, list)
            or "text" not in input_modalities
            or "text" not in output_modalities
        ):
            return frozenset()
    capabilities = {"text"}
    input_limit = entry.metadata.get("input_token_limit") or entry.metadata.get("context_length")
    if isinstance(input_limit, int) and input_limit >= min_long_context_tokens:
        capabilities.add("long_context")
    return frozenset(capabilities)


def build_capability_document(
    catalog: ModelCatalog,
    *,
    now: datetime | None = None,
    min_long_context_tokens: int = 32_768,
) -> dict[str, Any]:
    """Build a capability candidate and fail closed on conflicting metadata."""

    if not isinstance(catalog, ModelCatalog):
        raise TypeError("catalog must be a ModelCatalog")
    grouped: dict[tuple[str, str], list[ModelCatalogEntry]] = {}
    for entry in _current_entries(catalog, now):
        capabilities = derive_capabilities(entry, min_long_context_tokens=min_long_context_tokens)
        if capabilities:
            grouped.setdefault((entry.provider_id, entry.model_id), []).append(entry)

    records: list[dict[str, Any]] = []
    for identity, entries in sorted(grouped.items()):
        derived = {derive_capabilities(entry, min_long_context_tokens=min_long_context_tokens) for entry in entries}
        if len(derived) != 1:
            raise ModelCatalogError(f"conflicting derived capabilities: {identity!r}")
        selected = max(entries, key=lambda entry: (_timestamp(entry.observed_at), getattr(entry, "provider_binding_id", "")))
        records.append(
            {
                "provider_id": identity[0],
                "model_id": identity[1],
                "capabilities": sorted(next(iter(derived))),
                "source": f"{selected.source}+discovery_metadata",
                "observed_at": selected.observed_at,
                "expires_at": selected.expires_at,
            }
        )
    return {"schema_version": 1, "entries": records}


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ModelCatalogError(f"timestamp must be timezone-aware: {value!r}")
    return parsed.astimezone(timezone.utc)


__all__ = [
    "build_alias_document",
    "build_capability_document",
    "canonical_model_id_for",
    "derive_capabilities",
]
