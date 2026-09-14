from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from src.dev_agent.coordination.protocol import (
    ArtifactReference,
    ControlAction,
    ControlRequest,
    CoordinationValidationError,
    GuardianActionRecord,
    GuardianActionStatus,
    HandoffNote,
    MailboxMessage,
    MailboxStatus,
    MessageKind,
    PeerRecord,
    PeerStatus,
)


def _timestamp() -> str:
    return datetime(2026, 9, 14, 12, 0, tzinfo=timezone.utc).isoformat()


def _artifact() -> ArtifactReference:
    return ArtifactReference(
        path="artifacts/handoffs/abc.json",
        sha256="a" * 64,
        size_bytes=123,
        kind="handoff",
        revision="1c2b226",
    )


def _peer(*, role: str = "agent", generation: int = 1, instance_id: str = "agent-1") -> PeerRecord:
    timestamp = _timestamp()
    return PeerRecord(
        role=role,
        instance_id=instance_id,
        generation=generation,
        pid=1234,
        revision="1c2b226",
        started_at=timestamp,
        heartbeat_at=timestamp,
        lease_until="2026-09-14T12:01:00+00:00",
        status=PeerStatus.READY,
        capabilities=("handoff", "mailbox"),
    )


def test_protocol_round_trip_preserves_peer_message_and_handoff() -> None:
    peer = _peer()
    restored_peer = PeerRecord.from_dict(peer.to_dict())
    assert restored_peer == peer

    message = MailboxMessage(
        message_id="message-1",
        sender_role=peer.role,
        sender_instance_id=peer.instance_id,
        sender_generation=peer.generation,
        recipient_role="codex",
        kind=MessageKind.HANDOFF,
        subject="Checkpoint",
        artifact_refs=(_artifact(),),
        correlation_id="run-1",
        requires_ack=True,
        idempotency_key="handoff-1",
        created_at=_timestamp(),
        expires_at="2026-09-14T13:00:00+00:00",
        status=MailboxStatus.PENDING,
    )
    restored_message = MailboxMessage.from_dict(message.to_dict())
    assert restored_message == message
    json.dumps(restored_message.to_dict(), ensure_ascii=False, allow_nan=False)

    note = HandoffNote(
        from_role="agent",
        to_role="codex",
        revision="1c2b226",
        run_id="run-1",
        task_id="task-1",
        correlation_id="run-1",
        completed=("protocol",),
        current_state="READY",
        pending=("review",),
        blockers=(),
        read_these=(_artifact(),),
        next_action="claim the handoff",
    )
    assert HandoffNote.from_dict(note.to_dict()) == note


def test_control_request_round_trip_binds_sender_and_target_generation():
    request = ControlRequest(
        request_id="request-1",
        sender_role="codex",
        sender_instance_id="codex-1",
        sender_generation=3,
        target_role="agent",
        target_generation=12,
        action=ControlAction.RESTART,
        desired_revision="abc123",
        reason="roll the agent to the verified runtime",
        created_at="2026-09-14T12:00:00+00:00",
        expires_at="2026-09-14T12:05:00+00:00",
        idempotency_key="restart-agent-12",
    )

    assert ControlRequest.from_dict(request.to_dict()) == request
    assert request.to_dict()["action"] == "RESTART"
    assert request.to_dict()["target_generation"] == 12


def test_guardian_action_round_trip_preserves_journal_identity_and_status():
    record = GuardianActionRecord(
        request_id="request-1",
        idempotency_key="restart-agent-12",
        request_digest="a" * 64,
        sender_role="codex",
        sender_instance_id="codex-1",
        sender_generation=3,
        target_role="agent",
        target_generation=12,
        action=ControlAction.RESTART,
        status=GuardianActionStatus.UNKNOWN,
        decision="ACCEPTED",
        decision_reason="request is valid for Guardian handling",
        created_at="2026-09-14T12:00:00+00:00",
        updated_at="2026-09-14T12:01:00+00:00",
        desired_revision="abc123",
        result_code="external_outcome_unknown",
        reconciliation_required=True,
    )

    restored = GuardianActionRecord.from_dict(record.to_dict())
    assert restored == record
    json.dumps(restored.to_dict(), ensure_ascii=False, allow_nan=False)


@pytest.mark.parametrize("action", ("KILL", "", None))
def test_control_request_rejects_unknown_action(action):
    with pytest.raises((ValueError, TypeError, CoordinationValidationError)):
        ControlRequest(
            request_id="request-1",
            sender_role="codex",
            sender_instance_id="codex-1",
            sender_generation=1,
            target_role="agent",
            target_generation=1,
            action=action,
            reason="bounded request",
            created_at="2026-09-14T12:00:00+00:00",
            idempotency_key="request-1",
        )


def test_protocol_rejects_unknown_status_and_unbounded_values() -> None:
    with pytest.raises((ValueError, TypeError, CoordinationValidationError)):
        _peer().to_dict() | {"status": "ONLINE"}
        PeerRecord.from_dict({**_peer().to_dict(), "status": "ONLINE"})

    with pytest.raises(ValueError):
        PeerRecord.from_dict({**_peer().to_dict(), "generation": 0})

    with pytest.raises(ValueError):
        PeerRecord.from_dict({**_peer().to_dict(), "capabilities": ["x" * 1000]})

    with pytest.raises(ValueError):
        ArtifactReference(
            path="../outside.json",
            sha256="a" * 64,
            size_bytes=1,
            kind="handoff",
        )


def test_protocol_rejects_secret_shaped_values_and_nan() -> None:
    with pytest.raises(ValueError):
        MailboxMessage(
            message_id="message-1",
            sender_role="agent",
            sender_instance_id="agent-1",
            sender_generation=1,
            recipient_role="codex",
            kind="NOTE",
            subject="api_key=should-not-be-stored",
            idempotency_key="message-1",
            created_at=_timestamp(),
        )

    with pytest.raises(ValueError):
        MailboxMessage(
            message_id="message-2",
            sender_role="agent",
            sender_instance_id="agent-1",
            sender_generation=1,
            recipient_role="codex",
            kind="NOTE",
            subject="normal",
            idempotency_key="message-2",
            created_at=_timestamp(),
            artifact_refs=({"size_bytes": float("nan")},),
        )


def test_handoff_note_is_bounded_and_structured() -> None:
    note = HandoffNote(
        from_role="agent",
        to_role="codex",
        revision="1c2b226",
        run_id="run-1",
        task_id="task-1",
        correlation_id="run-1",
        completed=(),
        current_state="READY",
        pending=(),
        blockers=(),
        read_these=(),
        next_action="review",
    )
    assert set(note.to_dict()) == {
        "from_role",
        "to_role",
        "revision",
        "run_id",
        "task_id",
        "correlation_id",
        "completed",
        "current_state",
        "pending",
        "blockers",
        "read_these",
        "next_action",
    }
