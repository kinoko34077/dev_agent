"""Provider-local Gemini conversation transcript support.

Gemini's REST API returns provider-specific fields such as ``thoughtSignature``
inside model parts.  Those fields must be replayed byte-for-byte for a later
function-response turn, but they are not part of the provider-neutral Kernel
protocol.  This module keeps the opaque model parts behind the Gemini adapter
boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from threading import RLock
from typing import Any


def extract_model_parts(raw: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return an immutable snapshot of the first candidate's raw model parts."""

    try:
        candidate = raw["candidates"][0]
        content = candidate["content"]
        parts = content["parts"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"missing candidate content: {exc}") from exc
    if not isinstance(parts, list) or not all(isinstance(part, Mapping) for part in parts):
        raise ValueError("candidate content parts must be a list of objects")
    return tuple(deepcopy(dict(part)) for part in parts)


def function_call_count(parts: tuple[dict[str, Any], ...]) -> int:
    """Count standard Gemini function-call parts without interpreting payloads."""

    return sum(isinstance(part.get("functionCall"), Mapping) for part in parts)


def thought_signature_count(parts: tuple[dict[str, Any], ...]) -> int:
    """Count signatures without exposing their opaque values."""

    return sum(bool(part.get("thoughtSignature")) for part in parts)


class GeminiTranscriptStore:
    """Thread-safe, provider-local model-turn store.

    The runtime currently keeps a concrete provider instance alive for a
    controller run.  A task's model turns are keyed by request id so a
    duplicate local invocation cannot append the same turn twice.  A resumed
    process that lacks this provider-local transcript is rejected by the
    adapter instead of fabricating a signature.
    """

    def __init__(self) -> None:
        self._turns: dict[str, dict[str, tuple[dict[str, Any], ...]]] = {}
        self._order: dict[str, list[str]] = {}
        self._replayed_signatures: dict[str, int] = {}
        self._lock = RLock()

    @staticmethod
    def key(task_id: str, request_id: str) -> str:
        return task_id or request_id

    def record(self, *, task_key: str, request_id: str, parts: tuple[dict[str, Any], ...]) -> None:
        if not task_key or not request_id or not parts:
            return
        with self._lock:
            task_turns = self._turns.setdefault(task_key, {})
            task_order = self._order.setdefault(task_key, [])
            if request_id not in task_turns:
                task_order.append(request_id)
            task_turns[request_id] = tuple(deepcopy(dict(part)) for part in parts)

    def history(self, *, task_key: str) -> tuple[tuple[dict[str, Any], ...], ...]:
        with self._lock:
            task_turns = self._turns.get(task_key, {})
            return tuple(
                tuple(deepcopy(dict(part)) for part in task_turns[request_id])
                for request_id in self._order.get(task_key, ())
                if request_id in task_turns
            )

    def clear(self, *, task_key: str) -> None:
        with self._lock:
            self._turns.pop(task_key, None)
            self._order.pop(task_key, None)
            self._replayed_signatures.pop(task_key, None)

    def mark_replayed(self, *, task_key: str, parts: tuple[dict[str, Any], ...]) -> None:
        with self._lock:
            self._replayed_signatures[task_key] = self._replayed_signatures.get(task_key, 0) + thought_signature_count(parts)

    def diagnostics(self, *, task_key: str) -> dict[str, int]:
        with self._lock:
            turns = [self._turns[task_key][request_id] for request_id in self._order.get(task_key, ()) if request_id in self._turns.get(task_key, {})]
            return {
                "model_turn_count": len(turns),
                "function_call_count": sum(function_call_count(parts) for parts in turns),
                "thought_signatures_received": sum(thought_signature_count(parts) for parts in turns),
                "thought_signatures_replayed": self._replayed_signatures.get(task_key, 0),
            }


__all__ = ["GeminiTranscriptStore", "extract_model_parts", "function_call_count", "thought_signature_count"]
