from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.dev_agent.coordination.protocol import MailboxMessage, MailboxStatus, MessageKind
from src.dev_agent.coordination.protocol import PeerRecord, PeerStatus
from src.dev_agent.coordination.store import CoordinationConflict, CoordinationStore


def _peer(*, role: str = "agent", instance_id: str = "agent-1", generation: int = 1) -> PeerRecord:
    return PeerRecord(
        role=role,
        instance_id=instance_id,
        generation=generation,
        pid=100,
        revision="rev-a",
        started_at="2026-09-14T12:00:00+00:00",
        heartbeat_at="2026-09-14T12:00:00+00:00",
        lease_until="2026-09-14T12:01:00+00:00",
        status=PeerStatus.READY,
        capabilities=("mailbox",),
    )


def _message(*, subject: str = "hello", key: str = "key-1") -> MailboxMessage:
    return MailboxMessage(
        message_id="message-1",
        sender_role="agent",
        sender_instance_id="agent-1",
        sender_generation=1,
        recipient_role="codex",
        kind=MessageKind.NOTE,
        subject=subject,
        idempotency_key=key,
        created_at="2026-09-14T12:00:00+00:00",
        expires_at="2026-09-14T13:00:00+00:00",
    )


def test_peer_presence_survives_reopen_and_expiry_is_degraded(tmp_path) -> None:
    path = tmp_path / "coordination.sqlite3"
    with CoordinationStore(path) as store:
        peer = _peer()
        assert store.register_peer(peer) == peer
        assert store.get_peer("agent", "agent-1", 1) == peer
        updated = store.heartbeat(
            "agent",
            "agent-1",
            1,
            heartbeat_at="2026-09-14T12:02:00+00:00",
            lease_until="2026-09-14T12:03:00+00:00",
        )
        assert updated.heartbeat_at == "2026-09-14T12:02:00+00:00"
        expired = store.expire_peer_leases(now="2026-09-14T12:04:00+00:00")
        assert [item.instance_id for item in expired] == ["agent-1"]
        assert store.get_peer("agent", "agent-1", 1).status is PeerStatus.DEGRADED

    with CoordinationStore(path) as reopened:
        assert reopened.get_peer("agent", "agent-1", 1).status is PeerStatus.DEGRADED


def test_coordination_schema_includes_guardian_action_journal_and_reopens(tmp_path) -> None:
    path = tmp_path / "coordination.sqlite3"
    with CoordinationStore(path) as store:
        version = store.connection.execute(
            "SELECT value FROM coordination_meta WHERE key='schema_version'"
        ).fetchone()["value"]
        table = store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='guardian_actions'"
        ).fetchone()

        assert version == str(CoordinationStore.SCHEMA_VERSION)
        assert table is not None

    with CoordinationStore(path) as reopened:
        assert reopened.list_guardian_actions() == ()


def test_generation_fences_old_peer_after_new_generation(tmp_path) -> None:
    with CoordinationStore(tmp_path / "coordination.sqlite3") as store:
        old = _peer()
        new = _peer(instance_id="agent-1", generation=2)
        store.register_peer(old)
        store.register_peer(new)

        with pytest.raises(CoordinationConflict):
            store.heartbeat(
                "agent",
                "agent-1",
                1,
                heartbeat_at="2026-09-14T12:02:00+00:00",
                lease_until="2026-09-14T12:03:00+00:00",
            )
        assert store.heartbeat(
            "agent",
            "agent-1",
            2,
            heartbeat_at="2026-09-14T12:02:00+00:00",
            lease_until="2026-09-14T12:03:00+00:00",
        ).generation == 2


def test_mailbox_idempotency_and_conflicting_reuse_are_durable(tmp_path) -> None:
    with CoordinationStore(tmp_path / "coordination.sqlite3") as store:
        first = store.enqueue(_message())
        duplicate = store.enqueue(_message())
        assert duplicate == first

        with pytest.raises(CoordinationConflict):
            store.enqueue(_message(subject="different"))

        assert store.get_message(first.message_id).status is MailboxStatus.PENDING


def test_mailbox_claim_ack_reclaim_is_at_least_once(tmp_path) -> None:
    with CoordinationStore(tmp_path / "coordination.sqlite3") as store:
        message = store.enqueue(_message())
        claimed = store.claim(
            "codex",
            consumer_instance_id="codex-1",
            now="2026-09-14T12:00:01+00:00",
            lease_seconds=30,
        )
        assert len(claimed) == 1
        assert claimed[0].status is MailboxStatus.CLAIMED
        assert claimed[0].attempt_count == 1

        with pytest.raises(CoordinationConflict):
            store.ack(message.message_id, consumer_instance_id="codex-2", now="2026-09-14T12:00:02+00:00")

        reclaimed = store.claim(
            "codex",
            consumer_instance_id="codex-2",
            now="2026-09-14T12:01:00+00:00",
            lease_seconds=30,
        )
        assert len(reclaimed) == 1
        assert reclaimed[0].attempt_count == 2
        acknowledged = store.ack(
            message.message_id,
            consumer_instance_id="codex-2",
            now="2026-09-14T12:01:01+00:00",
        )
        assert acknowledged.status is MailboxStatus.ACKED
        assert store.ack(
            message.message_id,
            consumer_instance_id="another-instance",
            now="2026-09-14T12:01:02+00:00",
        ).status is MailboxStatus.ACKED


def test_expired_message_is_not_claimed(tmp_path) -> None:
    with CoordinationStore(tmp_path / "coordination.sqlite3") as store:
        store.enqueue(
            MailboxMessage(
                **{
                    **_message().to_dict(),
                    "message_id": "expired",
                    "idempotency_key": "expired-key",
                    "expires_at": "2026-09-14T11:59:00+00:00",
                }
            )
        )
        assert store.claim(
            "codex",
            consumer_instance_id="codex-1",
            now="2026-09-14T12:00:00+00:00",
            lease_seconds=30,
        ) == []
        assert store.get_message("expired").status is MailboxStatus.EXPIRED
