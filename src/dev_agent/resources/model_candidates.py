"""Small, explicit materialization seam for credential-backed model candidates."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from collections.abc import Callable, Iterable
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


def current_no_charge_candidate_ids(
    binding: Any,
    catalog: ModelCatalog,
    profile_resolver: Callable[[str, str, str], Any],
    *,
    now=None,
) -> tuple[str, ...]:
    """Return exact, current no-charge discovered IDs for bounded prioritization.

    This is only candidate ordering input. It does not admit a model or
    replace any downstream billing, qualification, capability, quota, or
    privacy checks.
    """

    if not isinstance(catalog, ModelCatalog):
        raise TypeError("catalog must be a ModelCatalog")
    if not callable(profile_resolver):
        raise TypeError("profile_resolver must be callable")
    identities = []
    for entry in catalog.entries_for_binding(binding.provider_id, binding.binding_id, now=now):
        if not entry.is_text_generation_candidate():
            continue
        profile = profile_resolver(
            binding.provider_id,
            binding.credential_binding_id,
            entry.model_id,
        )
        is_current = getattr(profile, "is_current", None)
        if (
            profile is not None
            and getattr(profile, "no_charge_guaranteed", False) is True
            and callable(is_current)
            and bool(is_current() if now is None else is_current(now=now))
        ):
            identities.append(entry.model_id)
    return tuple(sorted(set(identities)))


def materialize_provider_bindings(
    binding: Any,
    catalog: ModelCatalog | None = None,
    *,
    expand_discovered_models: bool = False,
    preferred_model_ids: Iterable[str] = (),
    now=None,
    min_input_token_limit: int = 0,
) -> tuple[Any, ...]:
    """Expand one credential binding into bounded execution candidates.

    The original ``provider_binding_id`` remains the discovery/catalog lane.
    ``credential_binding_id`` remains the exact qualification and billing
    lane.  Each expanded candidate gets a distinct registry identity so two
    models sharing one credential do not collide in ProviderRegistry or
    durable dispatch intents.
    """

    if (
        not hasattr(binding, "candidate_model_ids")
        or not hasattr(binding, "credential_binding_id")
        or not hasattr(binding, "binding_id")
    ):
        raise TypeError("binding must be an OperationProviderBinding-like object")
    if catalog is not None and not isinstance(catalog, ModelCatalog):
        raise TypeError("catalog must be a ModelCatalog or None")
    if not isinstance(expand_discovered_models, bool):
        raise ValueError("expand_discovered_models must be a boolean")
    if isinstance(preferred_model_ids, (str, bytes)):
        raise ValueError("preferred_model_ids must be a collection of model IDs")
    try:
        preferred = tuple(dict.fromkeys(preferred_model_ids))
    except TypeError as exc:
        raise ValueError("preferred_model_ids must be an iterable of model IDs") from exc
    if len(preferred) > 128 or any(not isinstance(model_id, str) or not model_id.strip() for model_id in preferred):
        raise ValueError("preferred_model_ids must contain at most 128 non-empty strings")
    base_binding_id = binding.credential_binding_id
    models: set[str] = set(binding.candidate_model_ids)
    if expand_discovered_models:
        if catalog is None:
            raise ValueError("catalog is required when expand_discovered_models is true")
        models.update(
            entry.model_id
            for entry in catalog.entries_for_binding(binding.provider_id, binding.binding_id, now=now)
            if entry.is_text_generation_candidate(min_input_token_limit=min_input_token_limit)
        )
    # Keep the operator/configuration seed visible even when an aggregator
    # returns more than the bounded candidate limit.  Downstream admission
    # still decides whether the seed is usable; this only prevents a sorted
    # catalog page from silently evicting it before admission runs.
    preferred_set = set(preferred)
    ordered_models = (
        binding.model,
        *sorted(
            (model for model in models if model != binding.model),
            key=lambda model: (model not in preferred_set, model),
        ),
    )[:128]
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


__all__ = ["current_no_charge_candidate_ids", "execution_binding_id", "materialize_provider_bindings"]
