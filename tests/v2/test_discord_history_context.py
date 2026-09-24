from __future__ import annotations

from types import SimpleNamespace
import asyncio

from src.dev_agent.discord.auth import DiscordAuthorizer
from src.dev_agent.discord.binding import DiscordBindingKey, SQLiteDiscordBindingStore
from src.dev_agent.discord.conversation_log import ConversationLog, ConversationMessage
from src.dev_agent.discord.context import (
    build_bounded_context,
    conversation_message_from_discord,
    sync_history_once,
    sync_discord_history,
)
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


def test_conversation_message_preserves_numeric_discord_reply_reference() -> None:
    key = DiscordBindingKey("10", "20", "30")
    message = SimpleNamespace(
        id=1201,
        author=SimpleNamespace(id=42, bot=False, name="Human"),
        created_at=None,
        reference=SimpleNamespace(message_id=1100),
    )

    row = conversation_message_from_discord(
        message,
        binding_key=key,
        message_kind="NOTE",
        message_id="1201",
        content="その返信の件も",
        direction="inbound",
    )

    assert row.reply_to_message_id == "1100"


def test_history_sync_preserves_reply_reference_without_replaying_history(tmp_path) -> None:
    key = DiscordBindingKey("10", "20", "30")
    authorizer = DiscordAuthorizer(allowed_user_ids={"42"})
    message = SimpleNamespace(
        id=1301,
        author=SimpleNamespace(id=42, bot=False, name="Human"),
        content="その返信の件も",
        webhook_id=None,
        reference=SimpleNamespace(message_id=1200),
    )

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        log = ConversationLog(store)
        assert sync_history_once(
            [message],
            log,
            key,
            authorizer=authorizer,
            bot_user_id="99",
            current_message_id="9999",
        ) == 1

        row = log.list_context("10|20|30")[0]

    assert row.reply_to_message_id == "1200"


def test_bounded_context_preserves_message_metadata_for_core_inputs(tmp_path) -> None:
    key = DiscordBindingKey("10", "20", "30")
    row = _stored("1401", "対象のBot発言", direction="outbound")
    row = ConversationMessage(**(row.to_dict() | {"reply_to_message_id": "1300", "message_kind": "PROGRESS", "root_id": "root-1", "run_id": "run-1"}))
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        log = ConversationLog(store)
        log.append(row)
        context = build_bounded_context(log, key, current_message_id="1402")

    assert context[0].to_dict(include_metadata=True) == {
        "role": "assistant",
        "content": "対象のBot発言",
        "message_id": "1401",
        "created_at": row.created_at,
        "speaker_id": "99",
        "speaker_name": "Bot",
        "reply_to_message_id": "1300",
        "message_kind": "PROGRESS",
        "root_id": "root-1",
        "run_id": "run-1",
    }


def test_bounded_context_includes_reply_target_outside_recent_window(tmp_path) -> None:
    key = DiscordBindingKey("10", "20", "30")
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        log = ConversationLog(store)
        target = _stored("1500", "一週間前のBot発言", direction="outbound")
        log.append(target)
        for index in range(1, 5):
            log.append(_stored(str(1500 + index), f"最近の発言 {index}"))

        context = build_bounded_context(
            log,
            key,
            current_message_id="1600",
            limit=2,
            reply_to_message_id="1500",
            reply_depth=3,
        )

    assert any(item.message_id == "1500" for item in context)
    assert len(context) <= 5


def test_history_sync_cursor_is_durable_on_the_existing_state_store(tmp_path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        bindings = SQLiteDiscordBindingStore(store)
        key = DiscordBindingKey("10", "20", "30")
        assert bindings.get_history_sync(key) is None
        saved = bindings.save_history_sync(
            key,
            latest_synced_message_id="1700",
            oldest_seeded_message_id="1200",
            seeded=True,
        )
        assert saved.seeded is True
        loaded = bindings.get_history_sync(key)

    assert loaded is not None
    assert loaded.latest_synced_message_id == "1700"
    assert loaded.oldest_seeded_message_id == "1200"


def test_initial_history_seed_and_incremental_cursor_only_write_conversation_log(tmp_path) -> None:
    key = DiscordBindingKey("10", "20", "30")
    authorizer = DiscordAuthorizer(allowed_user_ids={"42"})
    human = SimpleNamespace(id=42, bot=False, name="Human")

    class Channel:
        def __init__(self) -> None:
            self.calls = []
            self.items = [
                SimpleNamespace(id=1800, author=human, content="過去の依頼", webhook_id=None),
                SimpleNamespace(id=1801, author=human, content="過去の補足", webhook_id=None),
            ]

        def history(self, **kwargs):
            self.calls.append(kwargs)

            async def _iterate():
                for item in self.items:
                    yield item

            return _iterate()

    channel = Channel()
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        bindings = SQLiteDiscordBindingStore(store)
        log = ConversationLog(store)
        inserted = asyncio.run(
            sync_discord_history(
                channel,
                log,
                bindings,
                key,
                authorizer=authorizer,
                bot_user_id="99",
                current_message_id="1900",
                current_message=SimpleNamespace(id=1900),
                history_after_factory=lambda value: SimpleNamespace(id=value),
            )
        )
        assert inserted == 2
        assert channel.calls[0]["limit"] == 500
        assert channel.calls[0]["before"].id == 1900
        assert channel.calls[0]["oldest_first"] is True
        assert bindings.get_history_sync(key).seeded is True

        channel.items = [SimpleNamespace(id=1901, author=human, content="新しい補足", webhook_id=None)]
        inserted = asyncio.run(
            sync_discord_history(
                channel,
                log,
                bindings,
                key,
                authorizer=authorizer,
                bot_user_id="99",
                current_message_id="1902",
                current_message=SimpleNamespace(id=1902),
                history_after_factory=lambda value: SimpleNamespace(id=value),
            )
        )
        assert inserted == 1
        assert channel.calls[1]["after"].id == 1900
        assert channel.calls[1]["limit"] == 100
        assert [row.content for row in log.list_context("10|20|30")] == ["過去の依頼", "過去の補足", "新しい補足"]
        assert bindings.get_history_sync(key).latest_synced_message_id == "1902"


def test_incremental_history_does_not_skip_a_full_page_tail(tmp_path) -> None:
    key = DiscordBindingKey("10", "20", "30")
    authorizer = DiscordAuthorizer(allowed_user_ids={"42"})
    human = SimpleNamespace(id=42, bot=False, name="Human")

    class Channel:
        def __init__(self, items):
            self.items = list(items)
            self.calls = []

        def history(self, **kwargs):
            self.calls.append(kwargs)
            after = kwargs.get("after")
            after_id = int(getattr(after, "id", after or 0))
            before = kwargs.get("before")
            before_id = int(getattr(before, "id", before or 2**63))
            limit = int(kwargs["limit"])
            selected = [item for item in self.items if after_id < int(item.id) < before_id][:limit]

            async def _iterate():
                for item in selected:
                    yield item

            return _iterate()

    messages = [
        SimpleNamespace(id=message_id, author=human, content=f"message-{message_id}", webhook_id=None)
        for message_id in range(1001, 1251)
    ]
    channel = Channel(messages)

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        bindings = SQLiteDiscordBindingStore(store)
        log = ConversationLog(store)
        bindings.save_history_sync(
            key,
            latest_synced_message_id="1000",
            oldest_seeded_message_id="900",
            seeded=True,
        )

        first = asyncio.run(
            sync_discord_history(
                channel,
                log,
                bindings,
                key,
                authorizer=authorizer,
                bot_user_id="99",
                current_message_id="1251",
                history_after_factory=lambda value: SimpleNamespace(id=value),
            )
        )
        assert first == 100
        assert bindings.get_history_sync(key).latest_synced_message_id == "1100"

        second = asyncio.run(
            sync_discord_history(
                channel,
                log,
                bindings,
                key,
                authorizer=authorizer,
                bot_user_id="99",
                current_message_id="1251",
                history_after_factory=lambda value: SimpleNamespace(id=value),
            )
        )
        assert second == 100
        assert bindings.get_history_sync(key).latest_synced_message_id == "1200"

        third = asyncio.run(
            sync_discord_history(
                channel,
                log,
                bindings,
                key,
                authorizer=authorizer,
                bot_user_id="99",
                current_message_id="1251",
                history_after_factory=lambda value: SimpleNamespace(id=value),
            )
        )
        assert third == 50
        assert bindings.get_history_sync(key).latest_synced_message_id == "1251"
        assert sum(log.get(str(message_id)) is not None for message_id in range(1001, 1251)) == 250
