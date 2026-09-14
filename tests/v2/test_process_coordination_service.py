from __future__ import annotations

import json

import pytest

from src.dev_agent.coordination import ResumeCapsule, WorkAddress
from src.dev_agent.coordination.protocol import ControlAction, GuardianActionStatus, HandoffNote, MessageKind, PeerStatus
from src.dev_agent.coordination.service import CoordinationSnapshot, ProcessCoordinationService


def _note() -> HandoffNote:
    return HandoffNote(
        from_role="agent",
        to_role="codex",
        revision="rev-a",
        run_id="run-1",
        task_id="task-1",
        correlation_id="corr-1",
        completed=("stage-c",),
        current_state="READY",
        pending=("review",),
        blockers=(),
        read_these=(),
        next_action="claim the handoff",
    )


def test_service_attach_handoff_claim_ack_survives_reopen(tmp_path) -> None:
    with ProcessCoordinationService(data_dir=tmp_path) as service:
        agent = service.attach_peer(
            "agent",
            revision="rev-a",
            capabilities=("handoff",),
            instance_id="agent-1",
            now="2026-09-14T12:00:00+00:00",
            lease_seconds=60,
        )
        codex = service.attach_peer(
            "codex",
            revision="rev-a",
            capabilities=("review",),
            instance_id="codex-1",
            now="2026-09-14T12:00:00+00:00",
            lease_seconds=60,
        )
        assert agent.status is PeerStatus.STARTING
        service.set_peer_status(agent, PeerStatus.READY)
        service.set_peer_status(codex, PeerStatus.READY)

        reference, message = service.send_handoff(
            agent,
            _note(),
            idempotency_key="handoff-1",
            expires_at="2026-09-14T13:00:00+00:00",
        )
        assert reference.sha256
        assert reference.size_bytes > 0
        assert message.artifact_refs == (reference,)

        claimed = service.claim_messages(codex, now="2026-09-14T12:00:01+00:00")
        assert len(claimed) == 1
        loaded = service.read_handoff(reference)
        assert loaded == _note()
        service.ack_message(codex, claimed[0], now="2026-09-14T12:00:02+00:00")

    with ProcessCoordinationService(data_dir=tmp_path) as reopened:
        assert reopened.read_handoff(reference) == _note()
        assert reopened.store.get_message(message.message_id).status.value == "ACKED"


def test_service_uses_data_dir_lazily_and_keeps_coordination_db_separate(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DEV_AGENT_DATA_DIR", str(tmp_path / "runtime"))
    with ProcessCoordinationService() as service:
        assert service.paths.database == tmp_path / "runtime" / "coordination" / "coordination.sqlite3"
        assert service.paths.artifacts == tmp_path / "runtime" / "coordination" / "artifacts"
        assert service.paths.database.exists()
        assert not (tmp_path / "runtime" / "state.sqlite3").exists()


def test_artifact_read_rejects_path_escape_and_overwrite(tmp_path) -> None:
    with ProcessCoordinationService(data_dir=tmp_path) as service:
        reference = service.artifacts.put_json({"ok": True}, kind="checkpoint", revision="rev-a")
        assert json.loads(service.artifacts.read(reference)) == {"ok": True}

        with pytest.raises(ValueError):
            service.artifacts.read({**reference.to_dict(), "path": "../secret.json"})
        with pytest.raises(ValueError):
            service.artifacts.put_json({"api_key": "never"}, kind="checkpoint", revision="rev-a")


def test_service_does_not_allow_stale_peer_to_send(tmp_path) -> None:
    with ProcessCoordinationService(data_dir=tmp_path) as service:
        old = service.attach_peer(
            "agent",
            revision="rev-a",
            instance_id="agent-1",
            now="2026-09-14T12:00:00+00:00",
            lease_seconds=60,
        )
        service.attach_peer(
            "agent",
            revision="rev-b",
            instance_id="agent-1",
            now="2026-09-14T12:00:00+00:00",
            lease_seconds=60,
        )
        with pytest.raises(Exception):
            service.send_message(
                old,
                recipient_role="codex",
                kind="NOTE",
                subject="stale",
                idempotency_key="stale-1",
            )


def test_service_persists_resume_capsule_as_checkpoint_artifact(tmp_path) -> None:
    capsule = ResumeCapsule(
        work_address=WorkAddress.parse("5-B-8"),
        status="SUSPENDED_BY_INTERRUPT",
        objective="continue the coordination task",
        current_action="saving the checkpoint",
        completed=("protocol",),
        next_action="resume mailbox validation",
        resume_from="after protocol validation",
        blocked_by=(),
        owned_paths=("src/dev_agent/coordination/work.py",),
        checkpoint_revision="rev-a",
    )
    with ProcessCoordinationService(data_dir=tmp_path) as service:
        agent = service.attach_peer(
            "agent",
            revision="rev-a",
            capabilities=("checkpoint",),
            instance_id="agent-1",
            now="2026-09-14T12:00:00+00:00",
            lease_seconds=60,
        )
        service.set_peer_status(agent, PeerStatus.READY)
        reference, message = service.send_checkpoint(
            agent,
            recipient_role="codex",
            capsule=capsule,
            idempotency_key="checkpoint-1",
        )
        assert message.kind is MessageKind.CHECKPOINT
        assert reference.kind == "resume_capsule"
        assert service.read_resume_capsule(reference) == capsule


def test_service_persists_generation_fenced_control_request_in_mailbox(tmp_path) -> None:
    with ProcessCoordinationService(data_dir=tmp_path) as service:
        codex = service.attach_peer(
            "codex",
            revision="rev-a",
            capabilities=("control-request",),
            instance_id="codex-1",
            now="2026-09-14T12:00:00+00:00",
            lease_seconds=60,
        )
        service.set_peer_status(codex, PeerStatus.READY)
        reference, message = service.send_control_request(
            codex,
            target_role="agent",
            target_generation=12,
            action=ControlAction.RESTART,
            desired_revision="rev-b",
            reason="verified runtime update",
            idempotency_key="restart-agent-12",
            expires_at="2026-09-14T12:05:00+00:00",
        )

        assert reference.kind == "control_request"
        assert message.kind is MessageKind.CONTROL_REQUEST
        loaded = service.read_control_request(reference)
        assert loaded.action is ControlAction.RESTART
        assert loaded.sender_generation == codex.generation
        assert loaded.target_generation == 12


def test_service_evaluates_control_request_against_latest_peer_generations(tmp_path) -> None:
    with ProcessCoordinationService(data_dir=tmp_path) as service:
        codex = service.attach_peer(
            "codex",
            revision="rev-a",
            instance_id="codex-1",
            now="2026-09-14T12:00:00+00:00",
            lease_seconds=60,
        )
        agent = service.attach_peer(
            "agent",
            revision="rev-a",
            instance_id="agent-1",
            now="2026-09-14T12:00:00+00:00",
            lease_seconds=60,
        )
        service.set_peer_status(codex, PeerStatus.READY)
        service.set_peer_status(agent, PeerStatus.READY)
        reference, _message = service.send_control_request(
            codex,
            target_role="agent",
            target_generation=agent.generation,
            action=ControlAction.RESTART,
            reason="restart the current generation",
            idempotency_key="restart-current-agent",
        )
        evaluation = service.evaluate_control_request(
            service.read_control_request(reference),
            now="2026-09-14T12:01:00+00:00",
        )

        assert evaluation.decision.value == "ACCEPTED"
        assert evaluation.process_action is None


def test_service_snapshot_exposes_expired_peers_unacked_mailbox_and_guardian_journal(tmp_path) -> None:
    with ProcessCoordinationService(data_dir=tmp_path) as service:
        agent = service.attach_peer(
            "agent",
            revision="rev-a",
            capabilities=("checkpoint",),
            instance_id="agent-1",
            now="2026-09-14T12:00:00+00:00",
            lease_seconds=30,
        )
        codex = service.attach_peer(
            "codex",
            revision="rev-a",
            capabilities=("review",),
            instance_id="codex-1",
            now="2026-09-14T12:00:00+00:00",
            lease_seconds=300,
        )
        service.set_peer_status(agent, PeerStatus.READY)
        service.set_peer_status(codex, PeerStatus.READY)
        reference, message = service.send_checkpoint(
            agent,
            recipient_role="codex",
            capsule=ResumeCapsule(
                work_address=WorkAddress.parse("5-B-8"),
                status="RUNNING",
                objective="inspect coordination state",
                current_action="snapshot",
                completed=(),
                next_action="read the snapshot",
                resume_from="snapshot",
                blocked_by=(),
                owned_paths=(),
                checkpoint_revision="rev-a",
            ),
            idempotency_key="snapshot-checkpoint-1",
        )

        snapshot = service.snapshot(now="2026-09-14T12:01:00+00:00")

        assert isinstance(snapshot, CoordinationSnapshot)
        assert snapshot.observed_at == "2026-09-14T12:01:00+00:00"
        assert snapshot.expired_peer_ids == ("agent:agent-1:1",)
        assert snapshot.mailbox[0].message_id == message.message_id
        assert snapshot.mailbox[0].artifact_refs[0].sha256 == reference.sha256
        assert snapshot.guardian_actions == ()
        assert service.store.get_peer("agent", "agent-1", 1).status is PeerStatus.READY


def test_service_guardian_action_wrapper_persists_without_process_authority(tmp_path) -> None:
    with ProcessCoordinationService(data_dir=tmp_path) as service:
        codex = service.attach_peer(
            "codex",
            revision="rev-a",
            capabilities=("control-request",),
            instance_id="codex-1",
            now="2026-09-14T12:00:00+00:00",
            lease_seconds=60,
        )
        agent = service.attach_peer(
            "agent",
            revision="rev-a",
            capabilities=("guardian-target",),
            instance_id="agent-1",
            now="2026-09-14T12:00:00+00:00",
            lease_seconds=60,
        )
        service.set_peer_status(codex, PeerStatus.READY)
        service.set_peer_status(agent, PeerStatus.READY)
        reference, _message = service.send_control_request(
            codex,
            target_role="agent",
            target_generation=agent.generation,
            action=ControlAction.RESTART,
            reason="record a bounded Guardian intent",
            idempotency_key="guardian-wrapper-1",
        )

        record = service.submit_guardian_action(
            service.read_control_request(reference),
            now="2026-09-14T12:00:01+00:00",
        )

        assert record.status is GuardianActionStatus.PENDING
        assert record.decision == "ACCEPTED"
        service.store.transition_guardian_action(
            record.request_id,
            to_status=GuardianActionStatus.EXECUTING,
            updated_at="2026-09-14T12:00:02+00:00",
            expected_from={GuardianActionStatus.PENDING},
        )
        assert service.reconcile_guardian_action(
            record.request_id,
            now="2026-09-14T12:00:03+00:00",
        ).status is GuardianActionStatus.UNKNOWN
