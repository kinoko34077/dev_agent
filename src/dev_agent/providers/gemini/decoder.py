"""Decode the documented Gemini generateContent REST response shape."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...domain.protocol import ModelResponse, ToolCall
from ..base import ProviderError
from .transcript import extract_model_parts


def decode_generate_content(raw: Mapping[str, Any], *, model: str, request_id: str | None = None) -> ModelResponse:
    try:
        candidate = raw["candidates"][0]
        parts = extract_model_parts(raw)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProviderError(f"gemini response decode failed: missing candidate content: {exc}", category="provider_decode", retryable=False) from exc
    text_segments: list[str] = []
    calls: list[ToolCall] = []
    try:
        for part in parts:
            if "text" in part:
                text_segments.append(part["text"])
            elif "functionCall" in part:
                function = part["functionCall"]
                calls.append(ToolCall(tool_name=function["name"], arguments=function.get("args", {}), provider_call_id=function.get("id"), originating_request_id=request_id))
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError(f"gemini response decode failed: invalid part: {exc}", category="provider_decode", retryable=False) from exc
    if not text_segments and not calls:
        raise ProviderError("gemini response decode failed: no text or function call", category="provider_decode", retryable=False)
    usage = raw.get("usageMetadata", {})
    return ModelResponse(provider="gemini", model=model, finish_reason=candidate.get("finishReason", "stop"), text_segments=text_segments, tool_calls=calls, usage=usage)
