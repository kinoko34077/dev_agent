from __future__ import annotations

import pytest

from src.dev_agent.coordination import (
    DrainDecision,
    ExternalEffectState,
    PeerStatus,
    ResumeCapsule,
    WorkAddress,
)
from src.dev_agent.coordination.protocol import CoordinationConflict, MessageKind
from src.dev_agent.coordination.service import ProcessCoordinationService


def _capsule(*, status: str = "RUNNING", blocked_by: tuple[str, ...] = ()) -> ResumeCapsule:
    return ResumeCapsule(
        work_address=WorkAddress.parse("5-B-8"),
        status=status,
        objective="continue the coordination task",
        current_action="finish the current bounded operation",
        completed=("protocol",),
        next_action="resume from the saved checkpoint",
        resume_from="after the current operation",
        blocked_by=blocked_by,
        owned_paths=("src/dev_agent/coordination/work.py",),
        checkpoint_revision="rev-a",
    )


_TEST_NOW = "2026-09-15T12:00:30+00:00"


def _ready_service(tmp_path, monkeypatch):
    # Keep the lease-validity assertion independent of the CI wall clock.
    monkeypatch.setattr("src.dev_agent.coordination.service._now", lambda: _TEST_NOW)
    service = ProcessCoordinationService(data_dir=tmp_path)
    agent = service.attach_peer(
        "agent",
        revision="rev-a",
        capabilities=("checkpoint", "work-claim"),
        instance_id="agent-1",
        now="2026-09-15T12:00:00+00:00",
        lease_seconds=60,
    )
    codex = service.attach_peer(
        "codex",
        revision="rev-a",
        capabilities=("checkpoint",),
        instance_id="codex-1",
        now="2026-09-15T12:00:00+00:00",
        lease_seconds=60,
    )
    agent = service.set_peer_status(agent, PeerStatus.READY)
    service.set_peer_status(codex, PeerStatus.READY)
    return service, agent


def test_prepare_drain_persists_checkpoint_pauses_claims_and_marks_restart_ready(tmp_path, monkeypatch):
    service, agent = _ready_service(tmp_path, monkeypatch)
    try:
        result = service.prepare_drain(
            agent,
            capsule=_capsule(),
            recipient_role="codex",
            idempotency_key="drain-agent-1",
            external_effect_state=ExternalEffectState.NONE,
        )

        assert result.decision is DrainDecision.READY_TO_RESTART
        assert result.ready_to_restart is True
        assert result.peer.status is PeerStatus.RESTARTING
        assert result.message.kind is MessageKind.CHECKPOINT
        saved = service.read_resume_capsule(result.reference)
        assert saved.status == "READY_TO_RESTART"
        with pytest.raises(CoordinationConflict, match="work claims are paused"):
            service.assert_work_claim_allowed(result.peer)
    finally:
        service.close()


def test_unknown_external_effect_stays_draining_and_never_becomes_restart_ready(tmp_path, monkeypatch):
    service, agent = _ready_service(tmp_path, monkeypatch)
    try:
        result = service.prepare_drain(
            agent,
            capsule=_capsule(),
            recipient_role="codex",
            idempotency_key="drain-agent-unknown",
            external_effect_state=ExternalEffectState.UNKNOWN,
        )

        assert result.decision is DrainDecision.BLOCKED_EXTERNAL_EFFECT
        assert result.ready_to_restart is False
        assert result.peer.status is PeerStatus.DRAINING
        saved = service.read_resume_capsule(result.reference)
        assert saved.status == "DRAIN_BLOCKED"
        assert saved.blocked_by == ("external_effect_unknown",)
        with pytest.raises(CoordinationConflict, match="work claims are paused"):
            service.assert_work_claim_allowed(result.peer)
    finally:
        service.close()


def test_drain_can_be_completed_after_unknown_effect_is_reconciled(tmp_path, monkeypatch):
    service, agent = _ready_service(tmp_path, monkeypatch)
    try:
        blocked = service.prepare_drain(
            agent,
            capsule=_capsule(),
            recipient_role="codex",
            idempotency_key="drain-agent-reconcile-1",
            external_effect_state="UNKNOWN",
        )
        resumed = service.prepare_drain(
            blocked.peer,
            capsule=_capsule(status="DRAIN_BLOCKED", blocked_by=("external_effect_unknown",)),
            recipient_role="codex",
            idempotency_key="drain-agent-reconcile-2",
            external_effect_state=ExternalEffectState.COMPLETED,
        )

        assert resumed.decision is DrainDecision.READY_TO_RESTART
        assert resumed.peer.status is PeerStatus.RESTARTING
        assert service.read_resume_capsule(resumed.reference).status == "READY_TO_RESTART"
    finally:
        service.close()


def test_drain_rejects_unsupported_external_effect_state(tmp_path, monkeypatch):
    service, agent = _ready_service(tmp_path, monkeypatch)
    try:
        with pytest.raises(ValueError, match="external_effect_state"):
            service.prepare_drain(
                agent,
                capsule=_capsule(),
                recipient_role="codex",
                idempotency_key="drain-agent-invalid",
                external_effect_state="MAYBE",
            )
    finally:
        service.close()
