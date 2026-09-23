from __future__ import annotations

import pytest

from src.dev_agent.discord.intent import IntentKind, IntentProposal, resolve_plain_text, validate_intent


def test_plain_text_resolver_returns_chat_without_creating_task() -> None:
    proposal = resolve_plain_text("ありがとう", active_run=False, has_binding=False)

    assert proposal.kind is IntentKind.CHAT
    assert proposal.objective is None
    assert proposal.delay_seconds is None


def test_plain_text_resolver_returns_follow_up_for_active_run() -> None:
    proposal = resolve_plain_text("さっきのREADMEにも追記して", active_run=True, has_binding=True)

    assert proposal.kind is IntentKind.FOLLOW_UP
    assert proposal.objective == "さっきのREADMEにも追記して"


def test_plain_text_resolver_returns_new_request_without_binding() -> None:
    proposal = resolve_plain_text("READMEを確認して", active_run=False, has_binding=False)

    assert proposal.kind is IntentKind.NEW_REQUEST
    assert proposal.objective == "READMEを確認して"


def test_plain_text_resolver_extracts_bounded_wait() -> None:
    proposal = resolve_plain_text("20秒待ってから返事して", active_run=False, has_binding=False)

    assert proposal.kind is IntentKind.WAIT
    assert proposal.delay_seconds == 20


def test_intent_validation_rejects_unsafe_or_incoherent_proposals() -> None:
    with pytest.raises(ValueError):
        validate_intent(
            IntentProposal(kind=IntentKind.WAIT, objective="wait", delay_seconds=None),
            has_binding=False,
        )
    with pytest.raises(ValueError):
        validate_intent(
            IntentProposal(kind=IntentKind.CHAT, objective="approve this", delay_seconds=10),
            has_binding=False,
        )
