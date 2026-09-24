from __future__ import annotations

import pytest

from src.dev_agent.discord.adapter import DiscordIngressAdapter, DiscordMessage
from src.dev_agent.discord.auth import DiscordAuthorizer
from src.dev_agent.discord.binding import DiscordBindingKey, DiscordScopeState, SQLiteDiscordBindingStore
from src.dev_agent.discord.human import DiscordHumanAdapter
from src.dev_agent.discord.approval import DiscordApprovalAdapter
from src.dev_agent.human import HumanRequest, SQLiteHumanInteractionPort
from src.dev_agent.state.sqlite_store import SQLiteStateStore


def _request() -> HumanRequest:
    return HumanRequest(
        request_id="discord-human-request-1",
        root_id="root-discord-1",
        task_id="task-discord-1",
        attempt_id="attempt-discord-1",
        reason="仕様の選択が必要です",
        question="AとBのどちらにしますか？",
        context={"path": "src/dev_agent/discord/adapter.py"},
        allowed_answers=("A", "B"),
        response_shape={"decision": "A|B"},
    )


def _message(message_id: str = "900") -> DiscordMessage:
    return DiscordMessage(
        message_id=message_id,
        author_id="42",
        guild_id="10",
        channel_id="20",
        thread_id="30",
        content="この作業を続けて",
    )


def test_sqlite_discord_binding_and_message_idempotency_survive_restart(tmp_path):
    path = tmp_path / "discord-state.sqlite3"
    key = DiscordBindingKey(guild_id="10", channel_id="20", thread_id="30")

    with SQLiteStateStore(path) as store:
        bindings = SQLiteDiscordBindingStore(store)
        bindings.bind(key, root_id="root-1", run_id="run-1")
        assert bindings.mark_message_seen("900", binding_key=key, kind="NEW_REQUEST") is True
        assert bindings.mark_message_seen("900", binding_key=key, kind="NEW_REQUEST") is False
        assert bindings.record_delivery("request-1", "901") is True
        assert bindings.record_delivery("request-1", "901") is False
        bindings.save_scope(
            key,
            directory_scope="src/dev_agent",
            selected_files=("src/dev_agent/operation.py",),
        )
        assert bindings.request_id_for_delivery("901") == "request-1"

    with SQLiteStateStore(path) as reopened:
        bindings = SQLiteDiscordBindingStore(reopened)
        binding = bindings.lookup(key)
        assert binding is not None
        assert binding.root_id == "root-1"
        assert binding.run_id == "run-1"
        assert bindings.mark_message_seen("900", binding_key=key, kind="NEW_REQUEST") is False
        assert bindings.has_delivery("request-1", "901") is True
        assert bindings.get_scope(key) == DiscordScopeState(
            directory_scope="src/dev_agent",
            selected_files=("src/dev_agent/operation.py",),
        )
        columns = {
            row[1]
            for row in reopened.connection.execute("PRAGMA table_info(discord_ingress)")
        }
        assert "content" not in columns


def test_sqlite_ingress_adapter_deduplicates_after_restart(tmp_path):
    path = tmp_path / "discord-ingress.sqlite3"
    authorizer = DiscordAuthorizer(allowed_user_ids={"42"})

    with SQLiteStateStore(path) as store:
        adapter = DiscordIngressAdapter(
            authorizer=authorizer,
            bindings=SQLiteDiscordBindingStore(store),
        )
        assert adapter.accept(_message()) is not None

    with SQLiteStateStore(path) as reopened:
        adapter = DiscordIngressAdapter(
            authorizer=authorizer,
            bindings=SQLiteDiscordBindingStore(reopened),
        )
        assert adapter.accept(_message()) is None


def test_discord_human_adapter_persists_request_renders_and_correlates_response(tmp_path):
    path = tmp_path / "discord-human.sqlite3"
    request = _request()
    deliveries: list[tuple[str, str]] = []

    with SQLiteStateStore(path) as store:
        adapter = DiscordHumanAdapter(
            SQLiteHumanInteractionPort(store),
            authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
            deliver=lambda item, rendered: deliveries.append((item.request_id, rendered)),
            bindings=SQLiteDiscordBindingStore(store),
        )
        adapter.request_human(request)

        assert deliveries[0][0] == request.request_id
        assert "判断が必要です" in deliveries[0][1]
        assert "src/dev_agent/discord/adapter.py" not in deliveries[0][1]
        assert adapter.record_delivery(request_id=request.request_id, discord_message_id="902") is True
        assert adapter.record_delivery(request_id=request.request_id, discord_message_id="902") is False

        response = adapter.receive_response(
            request_id=request.request_id,
            author_id="42",
            response={"decision": "A"},
            decision="A",
        )
        assert response.responder == "discord:42"
        assert store.get_human_response(request.request_id) == response
        assert adapter.consume_response(request.request_id) == response
        with pytest.raises(ValueError, match="already consumed"):
            adapter.consume_response(request.request_id)


def test_discord_human_adapter_rejects_wrong_identity_and_unknown_request(tmp_path):
    path = tmp_path / "discord-human-auth.sqlite3"
    with SQLiteStateStore(path) as store:
        adapter = DiscordHumanAdapter(
            SQLiteHumanInteractionPort(store),
            authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
        )
        adapter.request_human(_request())

        with pytest.raises(PermissionError):
            adapter.receive_response(
                request_id="discord-human-request-1",
                author_id="codex",
                response={"decision": "A"},
                decision="A",
            )


def test_discord_human_adapter_uses_bounded_marker_for_free_text_response(tmp_path):
    path = tmp_path / "discord-human-free-text.sqlite3"
    request = HumanRequest(
        request_id="discord-free-text-1",
        root_id="root-free-text-1",
        task_id="task-free-text-1",
        attempt_id="attempt-free-text-1",
        reason="追加情報が必要です",
        question="補足を入力してください",
    )
    with SQLiteStateStore(path) as store:
        adapter = DiscordHumanAdapter(
            SQLiteHumanInteractionPort(store),
            authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
        )
        adapter.request_human(request)

        response = adapter.receive_response(
            request_id=request.request_id,
            author_id="42",
            response="この内容で進めてください",
            decision=None,
        )

        assert response.decision == "TEXT_RESPONSE"
        with pytest.raises(KeyError):
            adapter.receive_response(
                request_id="other-request",
                author_id="42",
                response={"decision": "A"},
                decision="A",
            )


def test_discord_human_adapter_maps_numbered_large_choice_reply_to_full_decision(tmp_path):
    path = tmp_path / "discord-human-numbered-choice.sqlite3"
    request = HumanRequest(
        request_id="discord-numbered-choice-1",
        root_id="root-numbered-choice-1",
        task_id="task-numbered-choice-1",
        attempt_id="attempt-numbered-choice-1",
        reason="仕様判断",
        question="番号または全文で返信してください",
        allowed_answers=tuple(f"complete-answer-{index}-" + ("x" * 90) for index in range(26)),
    )
    with SQLiteStateStore(path) as store:
        adapter = DiscordHumanAdapter(
            SQLiteHumanInteractionPort(store),
            authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
        )
        adapter.request_human(request)

        response = adapter.receive_response(
            request_id=request.request_id,
            author_id="42",
            response="2",
            decision=None,
        )

        assert response.decision == request.allowed_answers[1]
        assert response.response == "2"


def test_discord_human_adapter_does_not_accept_expert_actor_as_human(tmp_path):
    path = tmp_path / "discord-human-authority.sqlite3"
    with SQLiteStateStore(path) as store:
        adapter = DiscordHumanAdapter(
            SQLiteHumanInteractionPort(store),
            authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
        )
        adapter.request_human(_request())
        with pytest.raises(PermissionError):
            adapter.receive_response(
                request_id="discord-human-request-1",
                author_id="codex",
                response={"decision": "A"},
                decision="A",
            )
        assert store.get_human_response("discord-human-request-1") is None


def test_discord_approval_adapter_delegates_decision_without_owning_authority():
    calls: list[tuple[str, bool, str]] = []
    adapter = DiscordApprovalAdapter(
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
        submit=lambda approval_id, approved, actor: calls.append((approval_id, approved, actor)),
    )

    assert adapter.submit("approval-1", author_id="42", approved=True) is None
    assert calls == [("approval-1", True, "discord:42")]
    with pytest.raises(PermissionError):
        adapter.submit("approval-1", author_id="codex", approved=True)
    assert calls == [("approval-1", True, "discord:42")]
