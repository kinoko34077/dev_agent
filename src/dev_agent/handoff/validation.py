"""Validation entrypoint for durable or transport-loaded handoffs."""

from __future__ import annotations

from collections.abc import Mapping

from .protocol import HandoffEnvelope


def validate_handoff(value: HandoffEnvelope | Mapping[str, object]) -> HandoffEnvelope:
    if isinstance(value, HandoffEnvelope):
        return value
    if not isinstance(value, Mapping):
        raise TypeError("handoff must be an HandoffEnvelope or object")
    return HandoffEnvelope.from_dict(value)


__all__ = ["validate_handoff"]
