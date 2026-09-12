"""Strict, provider-specific normalization for ``codex exec --json`` output.

The Codex wire format is intentionally kept outside the shared AgentBackend
protocol.  This module extracts only bounded structural facts and usage
scalars; command text, model text, and aggregated tool output are not copied
into normalized events or durable evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import re
from typing import Any

from .protocol import AgentBackendEvent


_USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
_DEFAULT_MAX_RECORDS = 1024
_DEFAULT_MAX_LINE_CHARS = 2 * 1024 * 1024
_STRUCTURAL_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")


class CodexJsonlParseError(ValueError):
    """Raised when bounded Codex JSONL cannot be safely normalized."""


@dataclass(frozen=True)
class CodexJsonlParseResult:
    """Provider-specific parse result before any Kernel-level mapping."""

    thread_id: str | None
    events: tuple[AgentBackendEvent, ...]
    usage: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "usage", dict(self.usage))


def _text(value: Any, name: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CodexJsonlParseError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise CodexJsonlParseError(f"{name} is too long")
    return normalized


def _usage(value: Any) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CodexJsonlParseError("usage must be an object")
    normalized: dict[str, int] = {}
    for key in _USAGE_FIELDS:
        if key not in value:
            continue
        item = value[key]
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise CodexJsonlParseError(f"usage.{key} must be a non-negative integer")
        normalized[key] = item
    return normalized


def _structural_token(value: Any, name: str) -> str:
    normalized = _text(value, name)
    if _STRUCTURAL_TOKEN.fullmatch(normalized) is None:
        raise CodexJsonlParseError(f"{name} must be a structural token")
    return normalized


def _safe_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    """Extract only structural fields from one untrusted provider record."""

    payload: dict[str, Any] = {}
    event_type = record["type"]
    if event_type == "thread.started" and "thread_id" in record:
        payload["thread_id"] = _structural_token(record["thread_id"], "thread_id")

    item = record.get("item")
    if item is not None:
        if not isinstance(item, Mapping):
            raise CodexJsonlParseError("item must be an object")
        if "id" in item:
            payload["item_id"] = _structural_token(item["id"], "item.id")
        if "type" in item:
            payload["item_type"] = _structural_token(item["type"], "item.type")
        if "exit_code" in item:
            exit_code = item["exit_code"]
            if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                raise CodexJsonlParseError("item.exit_code must be an integer")
            payload["exit_code"] = exit_code

    return payload


def parse_codex_jsonl(
    text: str,
    *,
    session_id: str = "codex-jsonl",
    max_records: int = _DEFAULT_MAX_RECORDS,
    max_line_chars: int = _DEFAULT_MAX_LINE_CHARS,
) -> CodexJsonlParseResult:
    """Parse bounded Codex JSONL without retaining free-form model output."""

    if not isinstance(text, str):
        raise CodexJsonlParseError("Codex JSONL must be text")
    session_id = _text(session_id, "session_id")
    if isinstance(max_records, bool) or not isinstance(max_records, int) or max_records <= 0:
        raise ValueError("max_records must be a positive integer")
    if isinstance(max_line_chars, bool) or not isinstance(max_line_chars, int) or max_line_chars <= 0:
        raise ValueError("max_line_chars must be a positive integer")

    thread_id: str | None = None
    events: list[AgentBackendEvent] = []
    total_usage: dict[str, int] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        if len(line) > max_line_chars:
            raise CodexJsonlParseError("JSONL record is too long")
        if len(events) >= max_records:
            raise CodexJsonlParseError("too many JSONL records")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodexJsonlParseError("JSONL record is not valid JSON object") from exc
        if not isinstance(record, Mapping):
            raise CodexJsonlParseError("JSONL record is not a valid JSON object")
        event_type = _structural_token(record.get("type"), "record.type")
        if "thread_id" in record:
            current_thread = _structural_token(record["thread_id"], "thread_id")
            if thread_id is not None and current_thread != thread_id:
                raise CodexJsonlParseError("conflicting thread_id values")
            thread_id = current_thread
        payload = _safe_payload({**record, "type": event_type})
        if event_type == "turn.completed" and "usage" in record:
            for key, value in _usage(record["usage"]).items():
                total_usage[key] = total_usage.get(key, 0) + value
        events.append(
            AgentBackendEvent(
                session_id=session_id,
                sequence=len(events) + 1,
                event_type=event_type,
                payload=payload,
            )
        )
    return CodexJsonlParseResult(thread_id=thread_id, events=tuple(events), usage=total_usage)


__all__ = ["CodexJsonlParseError", "CodexJsonlParseResult", "parse_codex_jsonl"]
