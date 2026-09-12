"""Human-readable renderers for structured handoffs."""

from __future__ import annotations

import json
from typing import Any

from .protocol import HandoffEnvelope
from .validation import validate_handoff


def _sentence(value: str) -> str:
    value = value.rstrip()
    return value if value.endswith(("。", "！", "？")) else value + "。"


def _caution(value: str) -> str:
    value = value.rstrip("。")
    if not value.endswith("こと"):
        value += "こと"
    return value + "に注意すること。"


def _payload_text(envelope: HandoffEnvelope) -> str:
    if envelope.payload is not None:
        if isinstance(envelope.payload, str):
            return envelope.payload
        return json.dumps(envelope.payload, ensure_ascii=False, sort_keys=True, indent=2)
    if envelope.payload_reference is not None:
        return json.dumps({"payload_reference": dict(envelope.payload_reference)}, ensure_ascii=False, sort_keys=True, indent=2)
    return ""


def render_handoff(value: HandoffEnvelope, *, renderer: str = "kinotch-ja-v1") -> str:
    envelope = validate_handoff(value)
    if renderer != "kinotch-ja-v1":
        raise ValueError(f"unsupported handoff renderer: {renderer}")
    lines = [
        f"以下、{_sentence(envelope.subject)}",
        _sentence(envelope.instruction),
    ]
    lines.extend(f"ただし、{_sentence(condition)}" for condition in envelope.conditions)
    lines.extend(_caution(caution) for caution in envelope.cautions)
    lines.extend(_sentence(requirement) for requirement in envelope.requirements)
    lines.extend(("┈┈┈┈┈┈┈┈┈┈", _payload_text(envelope)))
    return "\n".join(lines)


__all__ = ["render_handoff"]
