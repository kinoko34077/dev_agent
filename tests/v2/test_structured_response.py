from __future__ import annotations

import json

import pytest

from src.dev_agent.domain.protocol import ModelResponse
from src.dev_agent.intelligence import decode_json_object as package_decode_json_object
from src.dev_agent.intelligence.structured_response import (
    StructuredResponseError,
    decode_json_object,
)


def _response(*, structured_output=None, text: str | None = None) -> ModelResponse:
    return ModelResponse(
        provider="test-provider",
        model="test-model",
        structured_output=structured_output,
        text_segments=[] if text is None else [text],
    )


def test_decode_json_object_accepts_structured_and_fenced_text_responses():
    structured = decode_json_object(
        _response(structured_output={"kind": "structured"}),
        role="critic",
        max_chars=1_000,
    )
    fenced = decode_json_object(
        _response(text="```json\n{" + '"kind":"text"' + "}\n```"),
        role="critic",
        max_chars=1_000,
    )

    assert structured == {"kind": "structured"}
    assert fenced == {"kind": "text"}


def test_intelligence_package_exposes_the_shared_decoder_boundary():
    assert package_decode_json_object is decode_json_object


def test_decode_json_object_rejects_invalid_or_oversized_provider_text():
    with pytest.raises(StructuredResponseError, match="has no structured JSON output"):
        decode_json_object(_response(), role="critic", max_chars=1_000)

    with pytest.raises(StructuredResponseError, match="exceeds the response limit"):
        decode_json_object(_response(text="x" * 1_001), role="critic", max_chars=1_000)

    with pytest.raises(StructuredResponseError, match="not valid JSON"):
        decode_json_object(_response(text="not-json"), role="critic", max_chars=1_000)

    with pytest.raises(StructuredResponseError, match="must be an object"):
        decode_json_object(_response(text=json.dumps(["not", "an", "object"])), role="critic", max_chars=1_000)


def test_decode_json_object_rejects_invalid_limits_without_exposing_response():
    with pytest.raises(StructuredResponseError, match="max_chars"):
        decode_json_object(_response(text="{}"), role="critic", max_chars=0)
