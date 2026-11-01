"""Bounded, non-authoritative OpenRouter free chat inventory normalization.

The OpenRouter model-list endpoint exposes more data than a durable inventory
needs.  This module accepts an already fetched JSON mapping and keeps only the
small set of facts needed for an issue-scoped public snapshot.  It never
performs network I/O and it never grants routing, billing, quota, health, or
qualification authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse


class OpenRouterInventoryError(ValueError):
    """The public model-list payload cannot be safely normalized."""


_MAX_MODELS = 2048
_MAX_LIST_ITEMS = 64
_MAX_STRING_CHARS = 256
_CHAT_ENDPOINT = "/api/v1/chat/completions"
_SOURCE_HOST = "openrouter.ai"


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise OpenRouterInventoryError(f"{name} must be an object")
    return value


def _bounded_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > _MAX_STRING_CHARS:
        raise OpenRouterInventoryError(f"{name} must be bounded non-empty text")
    return value.strip()


def _bounded_strings(value: Any, name: str, *, required: bool = True) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or len(value) > _MAX_LIST_ITEMS:
        raise OpenRouterInventoryError(f"{name} must be a bounded string list")
    result: list[str] = []
    for index, item in enumerate(value):
        text = _bounded_text(item, f"{name}[{index}]")
        if text not in result:
            result.append(text)
    if required and not result:
        raise OpenRouterInventoryError(f"{name} must not be empty")
    return result


def _positive_int(value: Any, name: str, *, required: bool = False) -> int | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 10_000_000_000:
        raise OpenRouterInventoryError(f"{name} must be a positive bounded integer")
    return value


def _zero_price(value: Any, name: str) -> bool:
    if isinstance(value, bool) or value is None:
        raise OpenRouterInventoryError(f"{name} must be a numeric price")
    try:
        price = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise OpenRouterInventoryError(f"{name} must be a numeric price") from exc
    if not price.is_finite() or price < 0:
        raise OpenRouterInventoryError(f"{name} must be a finite non-negative price")
    return price == 0


def _source_url(value: Any) -> str:
    source = _bounded_text(value, "source_url")
    parsed = urlparse(source)
    if parsed.scheme != "https" or parsed.hostname != _SOURCE_HOST:
        raise OpenRouterInventoryError("source_url must use the official OpenRouter HTTPS host")
    return source


def _observed_at(value: Any) -> str:
    observed = _bounded_text(value, "observed_at")
    try:
        parsed = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OpenRouterInventoryError("observed_at must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OpenRouterInventoryError("observed_at must include a timezone")
    return observed


def _candidate_entry(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    model_id = _bounded_text(raw.get("id"), "model.id")
    if not model_id.endswith(":free"):
        return None

    pricing = _mapping(raw.get("pricing"), "model.pricing")
    prompt = pricing.get("prompt")
    completion = pricing.get("completion")
    is_free = _zero_price(prompt, "model.pricing.prompt") and _zero_price(completion, "model.pricing.completion")

    architecture = _mapping(raw.get("architecture"), "model.architecture")
    output_modalities = _bounded_strings(architecture.get("output_modalities"), "model.output_modalities")
    input_modalities = _bounded_strings(architecture.get("input_modalities"), "model.input_modalities")
    endpoints = _bounded_strings(raw.get("endpoints"), "model.endpoints")
    if not is_free or "text" not in output_modalities or _CHAT_ENDPOINT not in endpoints:
        return None

    name = _bounded_text(raw.get("name"), "model.name")
    entry: dict[str, Any] = {
        "model_id": model_id,
        "name": name,
        "pricing_usd_per_token": {
            "prompt": str(prompt),
            "completion": str(completion),
        },
        "input_modalities": input_modalities,
        "output_modalities": output_modalities,
    }
    context_length = _positive_int(raw.get("context_length"), "model.context_length")
    max_completion_tokens = _positive_int(
        _mapping(raw.get("top_provider"), "model.top_provider").get("max_completion_tokens")
        if raw.get("top_provider") is not None else None,
        "model.max_completion_tokens",
    )
    supported_parameters = raw.get("supported_parameters")
    if supported_parameters is not None:
        entry["supported_parameters"] = _bounded_strings(
            supported_parameters, "model.supported_parameters", required=False
        )
    if context_length is not None:
        entry["context_length"] = context_length
    if max_completion_tokens is not None:
        entry["max_completion_tokens"] = max_completion_tokens
    return entry


def normalize_free_chat_inventory(
    payload: Mapping[str, Any],
    *,
    observed_at: str,
    source_url: str,
) -> dict[str, Any]:
    """Normalize a public OpenRouter ``/models`` response into a safe snapshot."""

    document = _mapping(payload, "model_list")
    raw_models = document.get("data")
    if not isinstance(raw_models, list) or not raw_models or len(raw_models) > _MAX_MODELS:
        raise OpenRouterInventoryError("model_list.data must be a bounded non-empty list")
    observed = _observed_at(observed_at)
    source = _source_url(source_url)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_models):
        model = _mapping(raw, f"model_list.data[{index}]")
        entry = _candidate_entry(model)
        if entry is None:
            continue
        model_id = entry["model_id"]
        if model_id in seen:
            raise OpenRouterInventoryError("duplicate selected model id")
        seen.add(model_id)
        entries.append(entry)
    entries.sort(key=lambda item: item["model_id"])
    return {
        "schema_version": 1,
        "observation_type": "public_model_inventory_snapshot",
        "observed_at": observed,
        "authority": "non_authoritative_observation_only",
        "openrouter_free_chat": {
            "source_url": source,
            "source_kind": "public OpenRouter model-list API response",
            "query_filters": {
                "max_price": 0,
                "output_modalities": "text",
                "local_conditions": [
                    "model ID ends with :free",
                    "prompt and completion price equal 0",
                    "chat completions endpoint is listed",
                ],
            },
            "entry_count": len(entries),
            "entries": entries,
        },
    }


__all__ = ["OpenRouterInventoryError", "normalize_free_chat_inventory"]
