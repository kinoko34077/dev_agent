"""Normalize mapping-based adapter payloads into the Core response contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.protocol import ModelResponse, ProtocolError, ToolCall
from .base import ProviderError


def _is_safe_diagnostic_scalar(val: Any) -> bool:
    if isinstance(val, bool):
        return False
    if isinstance(val, int):
        return -1000000 <= val <= 1000000
    if isinstance(val, str):
        if not val or len(val) > 64:
            return False
        for char in val:
            code = ord(char)
            is_upper = 65 <= code <= 90
            is_lower = 97 <= code <= 122
            is_digit = 48 <= code <= 57
            is_allowed_symbol = char in ('_', '.', ':', '-')
            if not (is_upper or is_lower or is_digit or is_allowed_symbol):
                return False
        return True
    return False


def normalize_response(raw: ModelResponse | Mapping[str, Any], *, provider: str, default_model: str) -> ModelResponse:
    if isinstance(raw, ModelResponse):
        return raw
    if not isinstance(raw, Mapping):
        raise ProviderError("provider response is not a mapping or ModelResponse")
    values = dict(raw)
    values.setdefault("provider", provider)
    values.setdefault("model", default_model)
    raw_error = values.get("raw_error")
    if raw_error:
        diagnostic = None
        if isinstance(raw_error, Mapping):
            for key in ("status", "code", "http_status"):
                candidate = raw_error.get(key)
                if candidate is not None and _is_safe_diagnostic_scalar(candidate):
                    diagnostic = str(candidate)
                    break
            if diagnostic is None:
                diagnostic = "structured_error"
        elif _is_safe_diagnostic_scalar(raw_error):
            diagnostic = str(raw_error)
        else:
            diagnostic = "structured_error"
        raise ProviderError(f"provider raw response error: {diagnostic}")
    try:
        values["tool_calls"] = [item.to_dict() if isinstance(item, ToolCall) else item for item in values.get("tool_calls", [])]
        response = ModelResponse.from_dict(values)
        if not (response.parts or response.text_segments or response.tool_calls or response.structured_output):
            raise ProviderError("provider response has no normalized content")
        return response
    except (ProtocolError, TypeError, ValueError) as exc:
        raise ProviderError(f"provider response normalization failed: {exc}") from exc
