"""Small, explicit materialization seam for credential-backed model candidates."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import Any

from .model_catalog import ModelCatalog


def execution_binding_id(credential_binding_id: str, model_id: str) -> str:
    """Create a deterministic registry identity for one binding/model pair."""

    if not isinstance(credential_binding_id, str) or not credential_binding_id.strip():
        raise ValueError("credential_binding_id must be a non-empty string")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("model_id must be a non-empty string")
    # Keep the human-readable lane while bounding arbitrary upstream model IDs
    # before they become durable resource/registry keys.
    model = model_id.strip()
    safe_model = "".join(character if character.isalnum() or character in ".-_" else "_" for character in model)
    suffix = sha256(model.encode("utf-8")).hexdigest()[:12]
    return f"{credential_binding_id.strip()}::model::{safe_model[:96]}::{suffix}"


def materialize_provider_bindings(
    binding: Any,
    catalog: ModelCatalog | None = None,
    *,
    expand_discovered_models: bool = False,
    now=None,
    min_input_token_limit: int = 0,
) -> tuple[Any, ...]:
    """Expand one credential binding into bounded execution candidates.

    The original ``provider_binding_id`` remains the exact qualification and
    billing lane via ``credential_binding_id``.  Each expanded candidate gets
    a distinct registry identity so two models sharing one credential do not
    collide in ProviderRegistry or durable dispatch intents.
    """

    if not hasattr(binding, "candidate_model_ids") or not hasattr(binding, "credential_binding_id"):
        raise TypeError("binding must be an OperationProviderBinding-like object")
    if catalog is not None and not isinstance(catalog, ModelCatalog):
        raise TypeError("catalog must be a ModelCatalog or None")
    if not isinstance(expand_discovered_models, bool):
        raise ValueError("expand_discovered_models must be a boolean")
    base_binding_id = binding.credential_binding_id
    models: set[str] = set(binding.candidate_model_ids)
    if expand_discovered_models:
        if catalog is None:
            raise ValueError("catalog is required when expand_discovered_models is true")
        models.update(
            entry.model_id
            for entry in catalog.entries_for_binding(binding.provider_id, base_binding_id, now=now)
            if entry.is_text_generation_candidate(min_input_token_limit=min_input_token_limit)
        )
    ordered_models = tuple(sorted(models))
    if not ordered_models:
        return ()
    if len(ordered_models) == 1 and ordered_models[0] == binding.model and binding.model_candidates is None:
        return (binding,)
    materialized = []
    for model_id in ordered_models:
        materialized.append(
            replace(
                binding,
                model=model_id,
                provider_binding_id=execution_binding_id(base_binding_id, model_id),
                qualification_binding_id=base_binding_id,
                model_candidates=None,
            )
        )
    return tuple(materialized)


__all__ = ["execution_binding_id", "materialize_provider_bindings"]
