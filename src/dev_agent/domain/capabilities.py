"""Domain-neutral vocabulary for provider execution capabilities."""

from __future__ import annotations


# This is the only canonical routing vocabulary.  Task competencies and
# policy traits live in ``intelligence.capabilities`` and are deliberately
# classified separately from this set.
CANONICAL_EXECUTION_CAPABILITIES = frozenset(
    {"text", "tool_call", "structured_output", "json", "long_context"}
)


__all__ = ["CANONICAL_EXECUTION_CAPABILITIES"]
