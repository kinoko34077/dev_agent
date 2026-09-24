from __future__ import annotations

import pytest

from src.dev_agent.discord.adapter import DiscordIngressAdapter, DiscordMessage
from src.dev_agent.discord.auth import DiscordAuthorizer
from src.dev_agent.discord.binding import InMemoryDiscordBindingStore
from src.dev_agent.discord.history import DiscordHistoryMessage
from src.dev_agent.discord.intent import IntentKind, IntentProposal, ProposalOnlyIntentResolver, resolve_plain_text, validate_intent
from src.dev_agent.domain.protocol import ModelResponse


def test_plain_text_resolver_returns_chat_without_creating_task() -> None:
    proposal = resolve_plain_text("ありがとう", active_run=False, has_binding=False)

    assert proposal.kind is IntentKind.CHAT
    assert proposal.objective is None
    assert proposal.delay_seconds is None


def test_plain_text_resolver_returns_follow_up_for_active_run() -> None:
    proposal = resolve_plain_text("さっきのREADMEにも追記して", active_run=True, has_binding=True)

    assert proposal.kind is IntentKind.FOLLOW_UP
    assert proposal.objective == "さっきのREADMEにも追記して"


def test_plain_text_resolver_keeps_terminal_conversation_follow_up_as_proposal() -> None:
    proposal = resolve_plain_text("やっぱりさっきの2個目だけ戻して", active_run=False, has_binding=True)

    assert proposal.kind is IntentKind.FOLLOW_UP
    assert proposal.objective == "やっぱりさっきの2個目だけ戻して"


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


def test_proposal_only_resolver_uses_bounded_context_and_strict_output() -> None:
    captured = []

    def model(request):
        captured.append(request)
        return ModelResponse(
            provider="ollama",
            model="qwen3.5:9b",
            structured_output={
                "kind": "CHAT",
                "confidence": 0.91,
                "target_message_id": None,
                "reason": "既存会話の確認質問",
            },
        )

    resolver = ProposalOnlyIntentResolver(model)
    proposal = resolver(
        "さっき何を変えた？",
        active_run=False,
        has_binding=True,
        history_context=(
            DiscordHistoryMessage(role="assistant", content="READMEを修正しました", message_id="100"),
        ),
    )

    assert proposal.kind is IntentKind.CHAT
    assert proposal.confidence == 0.91
    assert captured[0].response_schema is not None
    assert "READMEを修正しました" in captured[0].messages[1]["content"]
    assert captured[0].metadata["authority"] == "proposal_only"


def test_invalid_model_intent_falls_back_to_deterministic_resolution() -> None:
    resolver = ProposalOnlyIntentResolver(lambda _request: ModelResponse(provider="ollama", model="qwen", text_segments=["not-json"]))
    adapter = DiscordIngressAdapter(
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
        bindings=InMemoryDiscordBindingStore(),
        intent_resolver=resolver,
    )
    message = DiscordMessage(
        message_id="100",
        author_id="42",
        guild_id="10",
        channel_id="20",
        thread_id="30",
        content="READMEを確認して",
    )

    event = adapter.accept(message)

    assert event is not None
    assert event.kind.value == "NEW_REQUEST"
    assert event.intent_proposal is None
