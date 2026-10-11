"""Decode the documented Gemini generateContent REST response shape."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...domain.protocol import ModelResponse, ToolCall
from ..base import ProviderError, attach_decode_diagnostics
from .transcript import extract_model_parts


def decode_generate_content(raw: Mapping[str, Any], *, model: str, request_id: str | None = None) -> ModelResponse:
    # Gemini may internally migrate a deprecated request model without any
    # HTTP redirect.  A successful payload cannot inherit the pinned model's
    # reviewed qualification/billing identity if the Provider reports a
    # different effective modelVersion.  This is a post-Provider decode error:
    # preserve reconciliation-required/no-replay semantics.
    resolved_model = raw.get("modelVersion")
    if "modelVersion" in raw:
        if not isinstance(resolved_model, str) or not resolved_model.strip():
            failure = ProviderError("gemini response decode failed: invalid model version", category="provider_decode", retryable=False)
            raise attach_decode_diagnostics(failure, raw, decoder_branch="gemini.model_version_invalid")
        if resolved_model.strip() != model:
            failure = ProviderError("gemini response model identity differs from selected model", category="provider_decode", retryable=False)
            raise attach_decode_diagnostics(failure, raw, decoder_branch="gemini.model_version_mismatch")
    try:
        candidate = raw["candidates"][0]
        parts = extract_model_parts(raw)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        failure = ProviderError("gemini response decode failed: missing candidate content", category="provider_decode", retryable=False)
        raise attach_decode_diagnostics(failure, raw, decoder_branch="gemini.generate_content") from exc
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
        failure = ProviderError("gemini response decode failed: invalid part", category="provider_decode", retryable=False)
        raise attach_decode_diagnostics(failure, raw, decoder_branch="gemini.generate_content") from exc
    if not text_segments and not calls:
        failure = ProviderError("gemini response decode failed: no text or function call", category="provider_decode", retryable=False)
        raise attach_decode_diagnostics(failure, raw, decoder_branch="gemini.generate_content")
    usage = raw.get("usageMetadata", {})
    return ModelResponse(provider="gemini", model=model, finish_reason=candidate.get("finishReason", "stop"), text_segments=text_segments, tool_calls=calls, usage=usage)
