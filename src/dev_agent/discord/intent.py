"""Proposal-only plain-text intent resolution for the Discord Human UI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from uuid import uuid4

from .wait import parse_user_delay


class IntentKind(str, Enum):
    CHAT = "CHAT"
    NEW_REQUEST = "NEW_REQUEST"
    FOLLOW_UP = "FOLLOW_UP"
    READ_QUERY = "READ_QUERY"
    WAIT = "WAIT"


@dataclass(frozen=True)
class IntentProposal:
    kind: IntentKind
    objective: str | None = None
    delay_seconds: int | None = None
    confidence: str | float = "deterministic"

    def __post_init__(self) -> None:
        if not isinstance(self.kind, IntentKind):
            object.__setattr__(self, "kind", IntentKind(self.kind))
        if self.objective is not None:
            if not isinstance(self.objective, str) or not self.objective.strip() or len(self.objective) > 4_000:
                raise ValueError("objective must be bounded non-empty text when present")
            object.__setattr__(self, "objective", self.objective.strip())
        if self.delay_seconds is not None and (
            isinstance(self.delay_seconds, bool)
            or not isinstance(self.delay_seconds, int)
            or not 1 <= self.delay_seconds <= 3_600
        ):
            raise ValueError("delay_seconds must be between 1 and 3600")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (str, int, float)):
            raise ValueError("confidence must be a bounded string or number")
        if isinstance(self.confidence, (int, float)) and not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("numeric confidence must be between 0 and 1")
        if isinstance(self.confidence, str) and (not self.confidence.strip() or len(self.confidence) > 64):
            raise ValueError("confidence text must be bounded")


_CHAT_PHRASES = frozenset({"ありがとう", "ありがとうございます", "了解", "了解です", "助かった", "こんにちは", "こんばんは", "おはよう"})
_READ_PHRASES = frozenset({"status", "今何してる", "進捗", "状態"})
_TERMINAL_FOLLOW_UP_MARKERS = ("やっぱ", "戻して", "戻す", "さっき", "前に", "この前", "あの時", "その件")


def resolve_plain_text(
    text: str,
    *,
    active_run: bool,
    has_binding: bool,
    history_context: Sequence[Mapping[str, Any]] | None = None,
    reply_to_message_id: str | None = None,
    current_status: str | None = None,
) -> IntentProposal:
    """Resolve ordinary text only; explicit authority commands stay elsewhere."""

    # These fields are accepted so the deterministic fallback and the
    # proposal-only model resolver share one narrow callable boundary.  The
    # deterministic path intentionally ignores context and status: authority
    # fast paths must not depend on model availability or conversation memory.
    _ = (history_context, reply_to_message_id, current_status)

    if not isinstance(text, str) or not text.strip() or len(text) > 4_000:
        raise ValueError("text must be bounded non-empty text")
    if not isinstance(active_run, bool) or not isinstance(has_binding, bool):
        raise TypeError("active_run and has_binding must be boolean")
    normalized = text.strip()
    lowered = normalized.casefold()
    if lowered.startswith(("/", "割り込み", "停止して", "中止して", "キャンセル", "並行", "補足", "メモ")):
        raise ValueError("explicit commands must use deterministic routing")
    if lowered in _READ_PHRASES or normalized in {"今何してる", "進捗", "状態"}:
        return validate_intent(IntentProposal(IntentKind.READ_QUERY), has_binding=has_binding)
    seconds = parse_user_delay(normalized)
    if seconds is not None:
        return validate_intent(IntentProposal(IntentKind.WAIT, normalized, seconds), has_binding=has_binding)
    if normalized in _CHAT_PHRASES:
        return validate_intent(IntentProposal(IntentKind.CHAT), has_binding=has_binding)
    if active_run and has_binding:
        return validate_intent(IntentProposal(IntentKind.FOLLOW_UP, normalized), has_binding=has_binding)
    if has_binding and any(marker in normalized for marker in _TERMINAL_FOLLOW_UP_MARKERS):
        return validate_intent(IntentProposal(IntentKind.FOLLOW_UP, normalized), has_binding=has_binding)
    return validate_intent(IntentProposal(IntentKind.NEW_REQUEST, normalized), has_binding=has_binding)


_SEMANTIC_KINDS = frozenset({IntentKind.CHAT, IntentKind.NEW_REQUEST, IntentKind.FOLLOW_UP, IntentKind.READ_QUERY})
_SEMANTIC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": sorted(kind.value for kind in _SEMANTIC_KINDS)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "target_message_id": {"type": ["string", "null"]},
        "reason": {"type": "string", "maxLength": 240},
    },
    "required": ["kind", "confidence", "target_message_id", "reason"],
}


def _context_lines(history_context: Sequence[Mapping[str, Any]] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if history_context is None:
        return result
    for item in history_context:
        if hasattr(item, "to_dict") and callable(item.to_dict):
            try:
                item = item.to_dict(include_metadata=True)
            except TypeError:
                item = item.to_dict()
        if not isinstance(item, Mapping):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in {"human", "assistant"} or not isinstance(content, str) or not content.strip():
            continue
        result.append({"role": role, "content": content.strip()[:1_500]})
    return result[-20:]


class ProposalOnlyIntentResolver:
    """Validate a model-backed conversational intent proposal.

    The model callable is injected by the existing Provider/Operation
    composition.  This class owns no provider registry, queue, task state, or
    authority.  A provider failure is deliberately surfaced as ``ValueError``
    so ingress can use the deterministic resolver without changing routing
    safety.
    """

    def __init__(self, request_model: Callable[[Any], Any]) -> None:
        if not callable(request_model):
            raise TypeError("request_model must be callable")
        self._request_model = request_model

    def __call__(
        self,
        text: str,
        *,
        active_run: bool,
        has_binding: bool,
        history_context: Sequence[Mapping[str, Any]] | None = None,
        reply_to_message_id: str | None = None,
        current_status: str | None = None,
    ) -> IntentProposal:
        if not isinstance(text, str) or not text.strip() or len(text) > 4_000:
            raise ValueError("text must be bounded non-empty text")
        if not isinstance(active_run, bool) or not isinstance(has_binding, bool):
            raise TypeError("active_run and has_binding must be boolean")
        if text.lstrip().startswith(("/", "割り込み", "停止して", "中止して", "キャンセル", "並行", "補足", "メモ")):
            raise ValueError("explicit commands must use deterministic routing")
        context = _context_lines(history_context)
        request_cls = _model_request_type()
        request = request_cls(
            request_id=str(uuid4()),
            task_id=str(uuid4()),
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classify this Discord message as a proposal only. "
                        "Never authorize CANCEL, APPROVE, REJECT, INTERRUPT, credentials, or mutation. "
                        "Return only the supplied JSON schema."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "current_message": text[:4_000],
                            "active_run": active_run,
                            "has_binding": has_binding,
                            "current_status": str(current_status or "")[:128],
                            "reply_to_message_id": reply_to_message_id if isinstance(reply_to_message_id, str) else None,
                            "history": context,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            response_schema=_SEMANTIC_SCHEMA,
            max_output_tokens=256,
            sensitivity="normal",
            cost_ceiling=0.0,
            metadata={"purpose": "discord_intent_proposal", "authority": "proposal_only"},
        )
        try:
            response = self._request_model(request)
        except Exception as exc:
            raise ValueError("semantic intent resolver unavailable") from exc
        payload = getattr(response, "structured_output", None)
        if payload is None:
            segments = getattr(response, "text_segments", ())
            raw = segments[0] if isinstance(segments, (list, tuple)) and segments else ""
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("semantic intent proposal is not valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("semantic intent proposal must be an object")
        try:
            kind = IntentKind(str(payload["kind"]))
            confidence = float(payload["confidence"])
            reason = payload["reason"]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("semantic intent proposal fields are invalid") from exc
        if kind not in _SEMANTIC_KINDS or not 0.0 <= confidence <= 1.0:
            raise ValueError("semantic intent proposal is outside the allowed proposal set")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 240:
            raise ValueError("semantic intent reason is invalid")
        if kind is IntentKind.FOLLOW_UP and not has_binding:
            raise ValueError("semantic FOLLOW_UP requires an existing binding")
        objective = text.strip() if kind in {IntentKind.NEW_REQUEST, IntentKind.FOLLOW_UP} else None
        return validate_intent(
            IntentProposal(kind, objective, confidence=confidence),
            has_binding=has_binding,
        )


def _model_request_type():
    # Local import keeps deterministic Discord tests and the optional Gateway
    # dependency lightweight while still using the canonical provider request
    # contract when a runner injects a real ProviderDispatcher.
    from ..domain.protocol import ModelRequest

    return ModelRequest


def validate_intent(proposal: IntentProposal, *, has_binding: bool) -> IntentProposal:
    """Apply Host-safe structural rules to a proposal-only intent."""

    if not isinstance(proposal, IntentProposal):
        raise TypeError("proposal must be an IntentProposal")
    if not isinstance(has_binding, bool):
        raise TypeError("has_binding must be boolean")
    if proposal.kind is IntentKind.WAIT:
        if proposal.delay_seconds is None:
            raise ValueError("WAIT requires delay_seconds")
        return proposal
    if proposal.kind is IntentKind.CHAT:
        if proposal.objective is not None or proposal.delay_seconds is not None:
            raise ValueError("CHAT cannot carry work or wait fields")
        return proposal
    if proposal.kind is IntentKind.FOLLOW_UP:
        if not has_binding:
            raise ValueError("FOLLOW_UP requires an existing binding")
        if not proposal.objective:
            raise ValueError("FOLLOW_UP requires objective")
        return proposal
    if proposal.kind is IntentKind.NEW_REQUEST and not proposal.objective:
        raise ValueError("NEW_REQUEST requires objective")
    if proposal.kind is IntentKind.READ_QUERY and proposal.objective is not None:
        raise ValueError("READ_QUERY cannot carry objective")
    return proposal


__all__ = [
    "IntentKind",
    "IntentProposal",
    "ProposalOnlyIntentResolver",
    "resolve_plain_text",
    "validate_intent",
]
