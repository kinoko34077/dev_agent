from __future__ import annotations

import pytest

from src.dev_agent.coordination.guardian import GuardianDecision, GuardianPolicy
from src.dev_agent.coordination.protocol import ControlAction, ControlRequest, PeerRecord, PeerStatus


def _peer(*, role: str, instance_id: str, generation: int, status: PeerStatus = PeerStatus.READY) -> PeerRecord:
    return PeerRecord(
        role=role,
        instance_id=instance_id,
        generation=generation,
        pid=1000 + generation,
        revision="rev-a",
        started_at="2026-09-14T12:00:00+00:00",
        heartbeat_at="2026-09-14T12:00:00+00:00",
        lease_until="2026-09-14T12:05:00+00:00",
        status=status,
        capabilities=("coordination",),
    )


def _request(*, sender_generation: int = 1, target_generation: int = 12, expires_at: str | None = None) -> ControlRequest:
    return ControlRequest(
        request_id="request-1",
        sender_role="codex",
        sender_instance_id="codex-1",
        sender_generation=sender_generation,
        target_role="agent",
        target_generation=target_generation,
        action=ControlAction.RESTART,
        reason="restart after a verified update",
        created_at="2026-09-14T12:00:00+00:00",
        idempotency_key="restart-1",
        expires_at=expires_at,
    )


def test_guardian_policy_accepts_current_generation_without_executing_process_action():
    evaluation = GuardianPolicy().evaluate(
        _request(),
        peers=(_peer(role="codex", instance_id="codex-1", generation=1), _peer(role="agent", instance_id="agent-1", generation=12)),
        now="2026-09-14T12:01:00+00:00",
    )

    assert evaluation.decision is GuardianDecision.ACCEPTED
    assert evaluation.target_generation == 12
    assert evaluation.process_action is None


def test_guardian_rejects_stale_target_generation():
    evaluation = GuardianPolicy().evaluate(
        _request(target_generation=12),
        peers=(_peer(role="codex", instance_id="codex-1", generation=1), _peer(role="agent", instance_id="agent-1", generation=13)),
        now="2026-09-14T12:01:00+00:00",
    )

    assert evaluation.decision is GuardianDecision.STALE_REQUEST


def test_guardian_rejects_stale_sender_and_expired_request():
    stale_sender = GuardianPolicy().evaluate(
        _request(sender_generation=1),
        peers=(_peer(role="codex", instance_id="codex-1", generation=2), _peer(role="agent", instance_id="agent-1", generation=12)),
        now="2026-09-14T12:01:00+00:00",
    )
    expired = GuardianPolicy().evaluate(
        _request(expires_at="2026-09-14T12:00:30+00:00"),
        peers=(_peer(role="codex", instance_id="codex-1", generation=1), _peer(role="agent", instance_id="agent-1", generation=12)),
        now="2026-09-14T12:01:00+00:00",
    )

    assert stale_sender.decision is GuardianDecision.SENDER_NOT_CURRENT
    assert expired.decision is GuardianDecision.EXPIRED


def test_guardian_rejects_ambiguous_current_target_generation():
    evaluation = GuardianPolicy().evaluate(
        _request(),
        peers=(
            _peer(role="codex", instance_id="codex-1", generation=1),
            _peer(role="agent", instance_id="agent-1", generation=12),
            _peer(role="agent", instance_id="agent-2", generation=12),
        ),
        now="2026-09-14T12:01:00+00:00",
    )

    assert evaluation.decision is GuardianDecision.TARGET_AMBIGUOUS


@pytest.mark.parametrize("status", (PeerStatus.STOPPED, PeerStatus.DEGRADED))
def test_guardian_rejects_sender_that_is_not_active(status):
    evaluation = GuardianPolicy().evaluate(
        _request(),
        peers=(_peer(role="codex", instance_id="codex-1", generation=1, status=status), _peer(role="agent", instance_id="agent-1", generation=12)),
        now="2026-09-14T12:01:00+00:00",
    )

    assert evaluation.decision is GuardianDecision.SENDER_NOT_CURRENT
