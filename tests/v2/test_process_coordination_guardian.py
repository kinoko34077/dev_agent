from __future__ import annotations

from dataclasses import replace

import pytest

from src.dev_agent.coordination.guardian import GuardianActionService, GuardianDecision, GuardianPolicy
from src.dev_agent.coordination.protocol import (
    ControlAction,
    ControlRequest,
    GuardianActionStatus,
    PeerRecord,
    PeerStatus,
)
from src.dev_agent.coordination.store import CoordinationStore


def _peer(
    *,
    role: str,
    instance_id: str,
    generation: int,
    status: PeerStatus = PeerStatus.READY,
    lease_until: str = "2026-09-14T12:05:00+00:00",
) -> PeerRecord:
    return PeerRecord(
        role=role,
        instance_id=instance_id,
        generation=generation,
        pid=1000 + generation,
        revision="rev-a",
        started_at="2026-09-14T12:00:00+00:00",
        heartbeat_at="2026-09-14T12:00:00+00:00",
        lease_until=lease_until,
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


def test_guardian_rejects_expired_sender_and_target_even_when_generation_matches():
    expired_sender = GuardianPolicy().evaluate(
        _request(),
        peers=(
            _peer(role="codex", instance_id="codex-1", generation=1, lease_until="2026-09-14T12:05:00+00:00"),
            _peer(role="agent", instance_id="agent-1", generation=12, lease_until="2026-09-14T12:10:00+00:00"),
        ),
        now="2026-09-14T12:06:00+00:00",
    )
    expired_target = GuardianPolicy().evaluate(
        _request(),
        peers=(
            _peer(role="codex", instance_id="codex-1", generation=1, lease_until="2026-09-14T12:10:00+00:00"),
            _peer(role="agent", instance_id="agent-1", generation=12, lease_until="2026-09-14T12:05:00+00:00"),
        ),
        now="2026-09-14T12:06:00+00:00",
    )

    assert expired_sender.decision is GuardianDecision.SENDER_NOT_CURRENT
    assert expired_target.decision is not GuardianDecision.ACCEPTED


def test_guardian_treats_lease_boundary_as_expired():
    evaluation = GuardianPolicy().evaluate(
        _request(),
        peers=(
            _peer(role="codex", instance_id="codex-1", generation=1),
            _peer(role="agent", instance_id="agent-1", generation=12, lease_until="2026-09-14T12:01:00+00:00"),
        ),
        now="2026-09-14T12:01:00+00:00",
    )

    assert evaluation.decision is not GuardianDecision.ACCEPTED


@pytest.mark.parametrize(
    ("status", "lease_until", "expected"),
    (
        (PeerStatus.READY, "2026-09-14T12:01:01+00:00", True),
        (PeerStatus.READY, "2026-09-14T12:01:00+00:00", False),
        (PeerStatus.READY, "2026-09-14T11:59:00+00:00", False),
        (PeerStatus.DRAINING, "2026-09-14T12:10:00+00:00", True),
        (PeerStatus.STOPPED, "2026-09-14T12:10:00+00:00", False),
    ),
)
def test_peer_record_is_live_centralizes_status_and_lease_boundary(status, lease_until, expected):
    peer = _peer(role="agent", instance_id="agent-1", generation=12, status=status, lease_until=lease_until)

    assert peer.is_live("2026-09-14T12:01:00+00:00") is expected


def _store_with_current_peers(tmp_path):
    store = CoordinationStore(tmp_path / "coordination.sqlite3")
    store.register_peer(_peer(role="codex", instance_id="codex-1", generation=1))
    store.register_peer(_peer(role="agent", instance_id="agent-1", generation=12))
    return store


def test_guardian_action_journal_executes_an_accepted_intent_once(tmp_path):
    class _Executor:
        def __init__(self):
            self.calls = []

        def execute(self, request):
            self.calls.append(request.request_id)

    store = _store_with_current_peers(tmp_path)
    executor = _Executor()
    try:
        service = GuardianActionService(store, executor=executor)
        record = service.submit(_request(), now="2026-09-14T12:01:00+00:00")
        duplicate = service.submit(_request(), now="2026-09-14T12:02:00+00:00")

        assert record.status is GuardianActionStatus.COMPLETED
        assert record.result_code == "executor_completed"
        assert duplicate == record
        assert executor.calls == ["request-1"]
        assert store.get_guardian_action(request_id="request-1") == record
    finally:
        store.close()


def test_guardian_action_journal_persists_policy_rejection_without_execution(tmp_path):
    class _Executor:
        def execute(self, request):
            raise AssertionError("rejected intent must not execute")

    store = CoordinationStore(tmp_path / "coordination.sqlite3")
    store.register_peer(_peer(role="codex", instance_id="codex-1", generation=1))
    store.register_peer(_peer(role="agent", instance_id="agent-1", generation=13))
    try:
        record = GuardianActionService(store, executor=_Executor()).submit(
            _request(target_generation=12),
            now="2026-09-14T12:01:00+00:00",
        )

        assert record.status is GuardianActionStatus.REJECTED
        assert record.decision == "STALE_REQUEST"
        assert record.reconciliation_required is False
    finally:
        store.close()


def test_guardian_action_executor_failure_closes_to_unknown_without_retry(tmp_path):
    class _Executor:
        def __init__(self):
            self.calls = 0

        def execute(self, request):
            self.calls += 1
            raise RuntimeError("external result is ambiguous")

    store = _store_with_current_peers(tmp_path)
    executor = _Executor()
    try:
        service = GuardianActionService(store, executor=executor)
        record = service.submit(_request(), now="2026-09-14T12:01:00+00:00")
        duplicate = service.submit(_request(), now="2026-09-14T12:02:00+00:00")

        assert record.status is GuardianActionStatus.UNKNOWN
        assert record.result_code == "external_outcome_unknown"
        assert record.reconciliation_required is True
        assert duplicate == record
        assert executor.calls == 1
    finally:
        store.close()


def test_guardian_rollback_intent_is_idempotent_when_requested_twice(tmp_path):
    class _Executor:
        def __init__(self):
            self.calls = 0

        def execute(self, request):
            self.calls += 1

    store = _store_with_current_peers(tmp_path)
    executor = _Executor()
    request = replace(
        _request(),
        request_id="rollback-request-1",
        action=ControlAction.ROLLBACK,
        idempotency_key="rollback-intent-1",
    )
    try:
        service = GuardianActionService(store, executor=executor)
        first = service.submit(request, now="2026-09-14T12:01:00+00:00")
        duplicate = service.submit(request, now="2026-09-14T12:02:00+00:00")

        assert first.status is GuardianActionStatus.COMPLETED
        assert duplicate == first
        assert executor.calls == 1
    finally:
        store.close()


def test_guardian_action_recovery_fences_interrupted_execution(tmp_path):
    class _Executor:
        def __init__(self):
            self.calls = 0

        def execute(self, request):
            self.calls += 1

    store = _store_with_current_peers(tmp_path)
    executor = _Executor()
    try:
        request = _request()
        journal = GuardianActionService(store)
        pending = journal.submit(request, now="2026-09-14T12:01:00+00:00")
        store.transition_guardian_action(
            pending.request_id,
            to_status=GuardianActionStatus.EXECUTING,
            updated_at="2026-09-14T12:01:01+00:00",
            expected_from={GuardianActionStatus.PENDING},
        )

        resumed = GuardianActionService(store, executor=executor).submit(
            request,
            now="2026-09-14T12:02:00+00:00",
        )
        reconciled = journal.reconcile_interrupted(
            request.request_id,
            now="2026-09-14T12:03:00+00:00",
        )

        assert resumed.status is GuardianActionStatus.EXECUTING
        assert executor.calls == 0
        assert reconciled.status is GuardianActionStatus.UNKNOWN
        assert reconciled.reconciliation_required is True
    finally:
        store.close()
