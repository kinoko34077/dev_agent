from __future__ import annotations

from src.dev_agent.discord.conversation_log import ConversationLog, ConversationMessage
from src.dev_agent.state.sqlite_store import SQLiteStateStore


def _message(message_id: str, *, content: str, direction: str = "inbound", reply_to: str | None = None) -> ConversationMessage:
    return ConversationMessage(
        message_id=message_id,
        binding_key="10|20|30",
        guild_id="10",
        channel_id="20",
        thread_id="30",
        created_at=f"2026-09-24T00:00:0{message_id[-1]}+00:00",
        received_at=f"2026-09-24T00:00:0{message_id[-1]}+00:00",
        speaker_role="human" if direction == "inbound" else "assistant",
        speaker_id="42" if direction == "inbound" else "99",
        speaker_name="Human" if direction == "inbound" else "dev_agent",
        direction=direction,
        content=content,
        reply_to_message_id=reply_to,
        message_kind="NEW_REQUEST",
        root_id="root-1",
        run_id="run-1",
        source="discord",
    )


def test_conversation_log_is_idempotent_and_preserves_message_metadata(tmp_path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        log = ConversationLog(store)
        message = _message("1001", content="READMEを確認して")

        assert log.append(message) is True
        assert log.append(message) is False

        rows = log.list_context("10|20|30")
        assert rows == (message,)
        assert rows[0].reply_to_message_id is None
        assert rows[0].direction == "inbound"
        assert rows[0].root_id == "root-1"


def test_conversation_log_records_outbound_reply_relation(tmp_path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        log = ConversationLog(store)
        inbound = _message("1002", content="それもやって")
        outbound = _message("1003", content="受け付けました", direction="outbound", reply_to="1002")

        assert log.append(inbound) is True
        assert log.append(outbound) is True
        rows = log.list_context("10|20|30")

        assert [row.message_id for row in rows] == ["1002", "1003"]
        assert rows[1].speaker_role == "assistant"
        assert rows[1].reply_to_message_id == "1002"


def test_conversation_log_sanitizes_content_before_persistence(tmp_path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        log = ConversationLog(store)
        message = _message("1004", content="確認 api_key=not-for-storage")

        assert log.append(message) is True
        stored = log.list_context("10|20|30")[0]

        assert "not-for-storage" not in stored.content
        assert "[REDACTED]" in stored.content


def test_conversation_context_is_bounded_by_message_count_and_characters(tmp_path) -> None:
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        log = ConversationLog(store)
        for index in range(1, 6):
            assert log.append(_message(str(2000 + index), content=f"message-{index}")) is True

        rows = log.list_context("10|20|30", limit=3, max_chars=20)

        assert len(rows) == 2
        assert "".join(item.content for item in rows) == "message-1message-2"
