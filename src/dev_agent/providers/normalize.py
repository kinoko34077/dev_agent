"""Normalize mapping-based adapter payloads into the Core response contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..domain.protocol import ModelResponse, ProtocolError, ToolCall
from .base import ProviderError


def normalize_response(raw: ModelResponse | Mapping[str, Any], *, provider: str, default_model: str) -> ModelResponse:
    if isinstance(raw, ModelResponse):
        return raw
    if not isinstance(raw, Mapping):
        raise ProviderError("provider response is not a mapping or ModelResponse")
    values = dict(raw)
    values.setdefault("provider", provider)
    values.setdefault("model", default_model)
    try:
        values["tool_calls"] = [item.to_dict() if isinstance(item, ToolCall) else item for item in values.get("tool_calls", [])]
        return ModelResponse.from_dict(values)
    except (ProtocolError, TypeError, ValueError) as exc:
        raise ProviderError(f"provider response normalization failed: {exc}") from exc
