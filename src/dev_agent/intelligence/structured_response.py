"""Shared bounded decoding for provider responses that promise JSON objects."""

from __future__ import annotations

from collections.abc import Mapping
import json
import re
from typing import Any

from ..domain.protocol import ModelResponse


class StructuredResponseError(ValueError):
    """A provider response cannot be treated as a bounded JSON object."""


_FENCED_JSON = re.compile(
    r"^```(?:json)?\s*\r?\n(?P<body>.*?)\r?\n```$",
    re.IGNORECASE | re.DOTALL,
)


def decode_json_object(
    response: ModelResponse,
    *,
    role: str,
    max_chars: int,
) -> Mapping[str, Any]:
    """Decode one bounded object without retaining raw provider output."""

    if not isinstance(role, str) or not role.strip():
        raise StructuredResponseError("role must be a non-empty string")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise StructuredResponseError("max_chars must be a positive integer")
    label = role.strip()
    if not isinstance(response, ModelResponse):
        raise StructuredResponseError(f"{label} provider returned an invalid response type")
    if response.structured_output is not None:
        return dict(response.structured_output)

    text = "".join(response.text_segments or response.parts)
    if not text.strip():
        raise StructuredResponseError(f"{label} response has no structured JSON output")
    if len(text) > max_chars:
        raise StructuredResponseError(f"{label} response exceeds the response limit")
    candidate = text.strip()
    fenced = _FENCED_JSON.fullmatch(candidate)
    if fenced is not None:
        candidate = fenced.group("body").strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise StructuredResponseError(f"{label} response is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise StructuredResponseError(f"{label} response JSON must be an object")
    return dict(payload)


__all__ = ["StructuredResponseError", "decode_json_object"]
