from __future__ import annotations

import json

import pytest

from src.dev_agent.providers.openrouter import (
    OpenRouterFreeProvider,
    OpenRouterHttpProvider,
    OpenRouterInventoryError as ExportedInventoryError,
    normalize_free_chat_inventory as exported_normalizer,
)
from src.dev_agent.providers.openrouter.free_inventory import (
    OpenRouterInventoryError,
    normalize_free_chat_inventory,
)


def test_openrouter_package_exports_inventory_normalizer_without_removing_provider_exports():
    assert ExportedInventoryError is OpenRouterInventoryError
    assert exported_normalizer is normalize_free_chat_inventory
    assert OpenRouterFreeProvider.__module__.endswith("openrouter.provider")
    assert OpenRouterHttpProvider.__module__.endswith("openrouter.provider")


def _model(model_id: str, *, prompt: str = "0", completion: str = "0",
           input_modalities: list[str] | None = None,
           output_modalities: list[str] | None = None,
           endpoints: list[str] | None = None, **extra: object) -> dict[str, object]:
    return {
        "id": model_id,
        "name": f"Display {model_id}",
        "description": "must not be retained",
        "pricing": {"prompt": prompt, "completion": completion},
        "architecture": {
            "input_modalities": input_modalities or ["text", "image"],
            "output_modalities": output_modalities or ["text"],
        },
        "context_length": 32768,
        "top_provider": {"max_completion_tokens": 4096},
        "endpoints": endpoints or ["/api/v1/chat/completions"],
        "supported_parameters": ["max_tokens", "tools"],
        "secret_payload": "must-not-persist",
        **extra,
    }


def test_normalizer_selects_sorted_zero_price_text_chat_models_and_keeps_bounded_facts():
    payload = {
        "data": [
            _model("zeta/code:free"),
            _model("alpha/chat:free", input_modalities=["text"]),
            _model("paid/model", prompt="0.000001"),
            _model("image/model:free", output_modalities=["image"]),
            _model("no-chat/model:free", endpoints=["/api/v1/completions"]),
            _model("not-a-free-model"),
        ]
    }

    result = normalize_free_chat_inventory(
        payload,
        observed_at="2026-11-02T08:00:00+00:00",
        source_url="https://openrouter.ai/api/v1/models?max_price=0&output_modalities=text",
    )

    section = result["openrouter_free_chat"]
    assert section["entry_count"] == 2
    assert [entry["model_id"] for entry in section["entries"]] == [
        "alpha/chat:free",
        "zeta/code:free",
    ]
    assert section["entries"][0]["pricing_usd_per_token"] == {"prompt": "0", "completion": "0"}
    assert section["entries"][0]["context_length"] == 32768
    assert section["entries"][0]["supported_parameters"] == ["max_tokens", "tools"]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "description" not in serialized
    assert "top_provider" not in serialized
    assert "secret_payload" not in serialized
    assert "routing" not in serialized
    assert result["authority"] == "non_authoritative_observation_only"


def test_normalizer_rejects_malformed_candidate_fields_instead_of_guessing():
    malformed = _model("broken/model:free")
    malformed["pricing"] = {"prompt": "unknown", "completion": "0"}
    with pytest.raises(OpenRouterInventoryError, match="pricing"):
        normalize_free_chat_inventory(
            {"data": [malformed]},
            observed_at="2026-11-02T08:00:00+00:00",
            source_url="https://openrouter.ai/api/v1/models",
        )

    malformed = _model("broken/model:free")
    malformed["architecture"] = {"output_modalities": "text"}
    with pytest.raises(OpenRouterInventoryError, match="output_modalities"):
        normalize_free_chat_inventory(
            {"data": [malformed]},
            observed_at="2026-11-02T08:00:00+00:00",
            source_url="https://openrouter.ai/api/v1/models",
        )


def test_normalizer_rejects_duplicate_selected_ids_and_bad_root_or_source():
    model = _model("duplicate/model:free")
    with pytest.raises(OpenRouterInventoryError, match="duplicate"):
        normalize_free_chat_inventory(
            {"data": [model, dict(model)]},
            observed_at="2026-11-02T08:00:00+00:00",
            source_url="https://openrouter.ai/api/v1/models",
        )

    with pytest.raises(OpenRouterInventoryError, match="data"):
        normalize_free_chat_inventory(
            {"data": []},
            observed_at="2026-11-02T08:00:00+00:00",
            source_url="https://openrouter.ai/api/v1/models",
        )

    with pytest.raises(OpenRouterInventoryError, match="source_url"):
        normalize_free_chat_inventory(
            {"data": [model]},
            observed_at="2026-11-02T08:00:00+00:00",
            source_url="http://example.invalid/models",
        )


def test_normalizer_does_not_accept_arbitrary_payload_keys_or_nonzero_free_prices():
    payload = {"data": [_model("paid/model:free", completion="0.01", arbitrary="x")]}
    result = normalize_free_chat_inventory(
        payload,
        observed_at="2026-11-02T08:00:00+00:00",
        source_url="https://openrouter.ai/api/v1/models",
    )
    assert result["openrouter_free_chat"]["entries"] == []
    assert "arbitrary" not in json.dumps(result)
