from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import json

import pytest

from src.dev_agent.discord.conversation_archive import archive_eligible_messages, search_archive
from src.dev_agent.discord.conversation_log import ConversationLog, ConversationMessage
from src.dev_agent.state.sqlite_store import SQLiteStateStore


def _message(message_id: str, created_at: datetime, content: str) -> ConversationMessage:
    stamp = created_at.isoformat()
    return ConversationMessage(
        message_id=message_id,
        binding_key="10|20|30",
        guild_id="10",
        channel_id="20",
        thread_id="30",
        created_at=stamp,
        received_at=stamp,
        speaker_role="human",
        speaker_id="42",
        speaker_name="Human",
        direction="inbound",
        content=content,
        reply_to_message_id=None,
        message_kind="NEW_REQUEST",
        root_id=None,
        run_id=None,
        source="discord",
    )


def test_archive_writes_verified_jsonl_gzip_manifest_then_removes_rows(tmp_path) -> None:
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        log = ConversationLog(store)
        assert log.append(_message("1001", now - timedelta(days=31), "old README context")) is True
        assert log.append(_message("1002", now - timedelta(days=1), "recent context")) is True

        manifest = archive_eligible_messages(log, tmp_path / "archive", now=now)

        assert manifest.count == 1
        assert manifest.archive_file.endswith(".jsonl.gz")
        assert [item.message_id for item in log.list_context("10|20|30")] == ["1002"]
        with gzip.open(tmp_path / "archive" / manifest.archive_file, "rt", encoding="utf-8") as handle:
            assert json.loads(handle.readline())["message_id"] == "1001"

        assert search_archive(tmp_path / "archive", "10|20|30", "README")[0].message_id == "1001"


def test_archive_failure_keeps_active_rows(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        log = ConversationLog(store)
        assert log.append(_message("1003", now - timedelta(days=31), "keep on failure")) is True

        def fail_replace(*_args, **_kwargs):
            raise OSError("archive write failed")

        monkeypatch.setattr("src.dev_agent.discord.conversation_archive.os.replace", fail_replace)
        with pytest.raises(OSError, match="archive write failed"):
            archive_eligible_messages(log, tmp_path / "archive", now=now)

        assert [item.message_id for item in log.list_context("10|20|30")] == ["1003"]
