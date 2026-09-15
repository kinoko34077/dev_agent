from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.dev_agent.coordination.protocol import PeerRecord, PeerStatus
from src.dev_agent.domain.protocol import IntelligenceTier, RiskLevel, Task, TaskType
from src.dev_agent.intelligence.policy import TaskIntelligencePolicy
from src.dev_agent.intelligence.role_manifest import (
    RoleAssignmentError,
    RoleInstance,
    RoleManifest,
    RoleTaskAdmission,
    RoleTaskAssignment,
    builtin_role_manifests,
    validate_assignment_set,
)


def _timestamp(hour: int = 12) -> str:
    return datetime(2026, 9, 16, hour, 0, tzinfo=timezone.utc).isoformat()


def _peer(
    *,
    instance_id: str,
    generation: int = 1,
    lease_until: str = "2026-09-16T13:00:00+00:00",
) -> PeerRecord:
    return PeerRecord(
        role="agent",
        instance_id=instance_id,
        generation=generation,
        pid=1234,
        revision="029d34986cb297b4865637b353063683d103a1ad",
        started_at=_timestamp(),
        heartbeat_at=_timestamp(),
        lease_until=lease_until,
        status=PeerStatus.READY,
        capabilities=("role", "task-ownership"),
    )


def _implementer_manifest() -> RoleManifest:
    return RoleManifest(
        role_id="implementer",
        responsibility="narrow implementation proposal",
        allowed_task_types=(TaskType.WORKER.value,),
        required_capabilities=("coding", "testing"),
        minimum_intelligence_tier=IntelligenceTier.L1,
        maximum_intelligence_tier=IntelligenceTier.L1,
        autonomy_ceiling="task_execution",
        tool_policy_ref="existing-task-tool-policy",
        provider_policy_ref="existing-resource-policy",
        privacy_ceiling="normal",
        risk_ceiling=RiskLevel.NORMAL,
        budget_ref="existing-budget-policy",
        max_concurrency=2,
        allowed_input_artifact_types=("task", "manifest"),
        allowed_output_artifact_types=("change_proposal", "test_proposal"),
        allowed_actions=("implement", "propose_tests"),
        forbidden_actions=("integrate", "approve_integration", "change_authority"),
        review_requirement="host",
    )


def test_role_manifest_round_trip_is_bounded_and_model_neutral():
    manifest = _implementer_manifest()

    restored = RoleManifest.from_dict(manifest.to_dict())

    assert restored == manifest
    assert "provider_id" not in manifest.to_dict()
    assert "model_id" not in manifest.to_dict()


def test_role_manifest_rejects_unknown_actions_and_unbounded_concurrency():
    with pytest.raises(ValueError, match="unknown action"):
        RoleManifest.from_dict({**_implementer_manifest().to_dict(), "allowed_actions": ["integrate"]})

    with pytest.raises(ValueError, match="max_concurrency"):
        RoleManifest.from_dict({**_implementer_manifest().to_dict(), "max_concurrency": 0})


def test_role_manifest_admits_only_matching_task_profile():
    manifest = _implementer_manifest()
    task = Task(
        objective="implement one bounded helper",
        task_type=TaskType.WORKER,
        required_capabilities=["coding"],
        risk=RiskLevel.NORMAL,
        sensitivity="normal",
    )
    decision = TaskIntelligencePolicy().decide(task)

    admission = manifest.validate_task(task, decision)

    assert isinstance(admission, RoleTaskAdmission)
    assert admission.role_id == "implementer"
    assert admission.task_id == task.task_id
    assert admission.intelligence_tier is IntelligenceTier.L1
    assert admission.to_dict()["provider_id"] is None

    with pytest.raises(ValueError, match="capabilit"):
        manifest.validate_task(
            Task(objective="needs architecture", task_type=TaskType.WORKER, required_capabilities=["architecture"]),
            decision,
        )


def test_role_instance_reuses_peer_identity_and_strict_lease():
    manifest = _implementer_manifest()
    peer = _peer(instance_id="agent-1")
    instance = RoleInstance.from_peer(manifest, peer)

    assert instance.role_id == "implementer"
    assert instance.instance_id == "agent-1"
    assert instance.generation == 1
    assert instance.is_current(peer, now=_timestamp()) is True
    assert instance.is_current(peer, now="2026-09-16T13:00:00+00:00") is False

    with pytest.raises(ValueError, match="identity"):
        instance.is_current(_peer(instance_id="agent-2"), now=_timestamp())


def test_validate_assignment_set_reuses_peer_lease_and_rejects_overlap():
    manifest = _implementer_manifest()
    peer_a = _peer(instance_id="agent-a")
    peer_b = _peer(instance_id="agent-b")
    assignments = (
        RoleTaskAssignment(
            task_id="task-a",
            role_id=manifest.role_id,
            instance_id=peer_a.instance_id,
            generation=peer_a.generation,
            owned_paths=("src/dev_agent/feature_a.py",),
        ),
        RoleTaskAssignment(
            task_id="task-b",
            role_id=manifest.role_id,
            instance_id=peer_b.instance_id,
            generation=peer_b.generation,
            owned_paths=("src/dev_agent/feature_b.py",),
        ),
    )

    assert validate_assignment_set(assignments, peers=(peer_a, peer_b), now=_timestamp()) == assignments

    with pytest.raises(RoleAssignmentError, match="overlap"):
        validate_assignment_set(
            (
                assignments[0],
                RoleTaskAssignment(
                    task_id="task-b",
                    role_id=manifest.role_id,
                    instance_id=peer_b.instance_id,
                    generation=peer_b.generation,
                    owned_paths=("src/dev_agent/feature_a.py",),
                ),
            ),
            peers=(peer_a, peer_b),
            now=_timestamp(),
        )

    expired = _peer(instance_id="agent-b", lease_until="2026-09-16T11:59:59+00:00")
    with pytest.raises(RoleAssignmentError, match="lease"):
        validate_assignment_set(assignments, peers=(peer_a, expired), now=_timestamp())


def test_builtin_role_manifests_are_three_non_integrating_roles():
    manifests = builtin_role_manifests()

    assert set(manifests) == {"planner", "implementer", "reviewer"}
    assert all("integrate" in manifest.forbidden_actions for manifest in manifests.values())
    assert manifests["reviewer"].autonomy_ceiling == "proposal_only"
