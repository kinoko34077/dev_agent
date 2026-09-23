from __future__ import annotations

from src.dev_agent.discord.adapter import DiscordIngressEvent, DiscordMessage, DiscordMessageKind
from src.dev_agent.discord.binding import DiscordBindingKey
from src.dev_agent.discord.core import DiscordCoreAdapter


def _event(content: str, kind: DiscordMessageKind) -> DiscordIngressEvent:
    message = DiscordMessage(
        message_id="900",
        author_id="42",
        guild_id="10",
        channel_id="20",
        thread_id="30",
        content=content,
    )
    return DiscordIngressEvent(
        message=message,
        kind=kind,
        binding_key=DiscordBindingKey("10", "20", "30"),
    )


def test_new_request_is_delegated_to_existing_operation_boundary():
    calls: list[tuple[str, DiscordIngressEvent]] = []
    adapter = DiscordCoreAdapter(
        submit_request=lambda content, event: calls.append((content, event)),
    )

    result = adapter.handle(_event("この作業を続けて", DiscordMessageKind.NEW_REQUEST))

    assert result is None
    assert calls[0][0] == "この作業を続けて"
    assert calls[0][1].binding_key == DiscordBindingKey("10", "20", "30")


def test_read_query_is_projection_and_does_not_submit_a_task():
    reads: list[DiscordIngressEvent] = []
    requests: list[DiscordIngressEvent] = []
    adapter = DiscordCoreAdapter(
        submit_request=lambda _content, event: requests.append(event),
        read_status=lambda event: reads.append(event) or {"current_action": "testing"},
    )

    assert adapter.handle(_event("今何してる", DiscordMessageKind.READ_QUERY)) == {"current_action": "testing"}
    assert len(reads) == 1
    assert requests == []


def test_chat_is_a_non_task_projection():
    adapter = DiscordCoreAdapter()

    assert adapter.handle(_event("ありがとう", DiscordMessageKind.CHAT)) == {
        "state": "CHAT",
        "text": "了解しました。",
    }


def test_wait_uses_the_durable_wait_boundary_and_proposal():
    from src.dev_agent.discord.intent import IntentKind, IntentProposal

    calls = []
    event = _event("20秒待って", DiscordMessageKind.WAIT)
    event = DiscordIngressEvent(
        message=event.message,
        kind=event.kind,
        binding_key=event.binding_key,
        intent_proposal=IntentProposal(IntentKind.WAIT, event.message.content, 20),
    )
    adapter = DiscordCoreAdapter(
        submit_wait=lambda content, received, proposal: calls.append((content, received, proposal)) or "waiting",
    )

    assert adapter.handle(event) == "waiting"
    assert calls[0][0] == "20秒待って"
    assert calls[0][2].delay_seconds == 20


def test_intervention_modes_share_existing_coordination_callback():
    calls: list[tuple[str, str, DiscordIngressEvent]] = []
    adapter = DiscordCoreAdapter(
        submit_coordination=lambda kind, content, event: calls.append((kind, content, event)),
    )

    for kind, content in (
        (DiscordMessageKind.NOTE, "/note keep this"),
        (DiscordMessageKind.PARALLEL, "/parallel investigate"),
        (DiscordMessageKind.INTERRUPT, "/interrupt now"),
        (DiscordMessageKind.CANCEL, "/cancel"),
    ):
        adapter.handle(_event(content, kind))

    assert [(kind, content) for kind, content, _event in calls] == [
        ("NOTE", "/note keep this"),
        ("PARALLEL", "/parallel investigate"),
        ("INTERRUPT", "/interrupt now"),
        ("CANCEL", "/cancel"),
    ]


def test_missing_core_callback_fails_closed_for_mutating_input():
    adapter = DiscordCoreAdapter()

    try:
        adapter.handle(_event("do work", DiscordMessageKind.NEW_REQUEST))
    except RuntimeError as exc:
        assert "Core" in str(exc)
    else:
        raise AssertionError("missing Core callback must not silently accept work")
