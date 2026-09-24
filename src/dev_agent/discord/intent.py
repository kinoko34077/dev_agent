"""Proposal-only plain-text intent resolution for the Discord Human UI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

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
    confidence: str = "deterministic"

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


_CHAT_PHRASES = frozenset({"ありがとう", "ありがとうございます", "了解", "了解です", "助かった", "こんにちは", "こんばんは", "おはよう"})
_READ_PHRASES = frozenset({"status", "今何してる", "進捗", "状態"})
_TERMINAL_FOLLOW_UP_MARKERS = ("やっぱ", "戻して", "戻す", "さっき", "前に", "この前", "あの時", "その件")


def resolve_plain_text(text: str, *, active_run: bool, has_binding: bool) -> IntentProposal:
    """Resolve ordinary text only; explicit authority commands stay elsewhere."""

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


__all__ = ["IntentKind", "IntentProposal", "resolve_plain_text", "validate_intent"]
