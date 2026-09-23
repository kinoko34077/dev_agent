from __future__ import annotations

from types import SimpleNamespace

from src.dev_agent.discord.auth import DiscordAuthorizer
from src.dev_agent.discord.binding import DiscordBindingKey
from src.dev_agent.discord.conversation_log import ConversationLog, ConversationMessage
from src.dev_agent.discord.context import build_bounded_context, sync_history_once
from src.dev_agent.state.sqlite_store import SQLiteStateStore


def _stored(message_id: str, content: str, *, direction: str = "inbound") -> ConversationMessage:
    return ConversationMessage(
        message_id=message_id,
        binding_key="10|20|30",
        guild_id="10",
        channel_id="20",
        thread_id="30",
        created_at=f"2026-09-24T00:00:{message_id[-2:]}+00:00",
        received_at=f"2026-09-24T00:00:{message_id[-2:]}+00:00",
        speaker_role="human" if direction == "inbound" else "assistant",
        speaker_id="42" if direction == "inbound" else "99",
        speaker_name="Human" if direction == "inbound" else "Bot",
        direction=direction,
        content=content,
        reply_to_message_id=None,
        message_kind="NEW_REQUEST",
        root_id=None,
        run_id=None,
        source="discord",
    )


def test_bounded_context_reads_local_log_and_excludes_current_message(tmp_path) -> None:
    key = DiscordBindingKey("10", "20", "30")
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        log = ConversationLog(store)
        assert log.append(_stored("1001", "READMEのエラー処理を直して")) is True
        assert log.append(_stored("1002", "作業を開始します", direction="outbound")) is True
        assert log.append(_stored("1003", "さっきの方もコメントして")) is True

        context = build_bounded_context(log, key, current_message_id="1003")

    assert [item.to_dict() for item in context] == [
        {"role": "human", "content": "READMEのエラー処理を直して"},
        {"role": "assistant", "content": "作業を開始します"},
    ]


def test_history_sync_records_rows_without_replaying_ingress(tmp_path) -> None:
    key = DiscordBindingKey("10", "20", "30")
    authorizer = DiscordAuthorizer(allowed_user_ids={"42"})
    human = SimpleNamespace(id=42, bot=False, name="Human")
    bot = SimpleNamespace(id=99, bot=True, name="dev_agent")
    foreign_bot = SimpleNamespace(id=100, bot=True, name="other-bot")
    messages = [
        SimpleNamespace(id=1001, author=human, content="READMEを確認", webhook_id=None),
        SimpleNamespace(id=1002, author=bot, content="受け付けました", webhook_id=None),
        SimpleNamespace(id=1003, author=foreign_bot, content="無関係", webhook_id=None),
    ]

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        log = ConversationLog(store)
        inserted = sync_history_once(
            messages,
            log,
            key,
            authorizer=authorizer,
            bot_user_id="99",
            current_message_id="1002",
        )

        assert inserted == 1
        assert [item.content for item in log.list_context("10|20|30")] == ["READMEを確認"]


def test_history_sync_is_idempotent_and_filters_unauthorized_human(tmp_path) -> None:
    key = DiscordBindingKey("10", "20", "30")
    authorizer = DiscordAuthorizer(allowed_user_ids={"42"})
    messages = [
        SimpleNamespace(id=1101, author=SimpleNamespace(id=7, bot=False, name="Other"), content="no", webhook_id=None),
        SimpleNamespace(id=1102, author=SimpleNamespace(id=42, bot=False, name="Human"), content="yes", webhook_id=None),
    ]

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        log = ConversationLog(store)
        assert sync_history_once(messages, log, key, authorizer=authorizer, bot_user_id="99", current_message_id="9999") == 1
        assert sync_history_once(messages, log, key, authorizer=authorizer, bot_user_id="99", current_message_id="9999") == 0
        assert [item.content for item in log.list_context("10|20|30")] == ["yes"]
