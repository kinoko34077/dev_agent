from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from scripts.devfarm_planning_bridge import DevelopmentPlanCandidate, DevelopmentPlanningBridge
from scripts.devfarm_plan_validation import validate_plan
from scripts.devfarm_errors import DevFarmError
from src.dev_agent.coordination.protocol import PeerRecord, PeerStatus
from src.dev_agent.domain.protocol import RiskLevel, Task, TaskType
from src.dev_agent.intelligence.planner import ChildTaskProposal, RootPlanningProposal
from src.dev_agent.intelligence.role_manifest import (
    RoleAssignmentError,
    RoleInstance,
    RoleTaskAssignment,
    builtin_role_manifests,
)


def _parent() -> Task:
    return Task(
        task_id=str(uuid4()),
        objective="coordinate a bounded multi-role slice",
        task_type=TaskType.REASONING,
    )


def _worker_spec(path: str) -> dict[str, object]:
    return {
        "manifest": {
            "task_type": "worker",
            "allowed_files": [path],
            "read_files": [path],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["gemini"],
            "outbound_files": [path],
            "requirements": ["keep the change narrow"],
            "acceptance": ["the focused test passes"],
            "test_commands": ["python -m pytest tests/v2 -q"],
            "max_attempts": 2,
            "output_contract": {},
        },
        "assignment": {
            "provider_id": "gemini",
            "provider_binding_id": "gemini:worker:free-3",
            "model_id": "gemini-3.5-flash-lite",
        },
    }


def _candidate(tmp_path, paths: tuple[str, ...] = ("src/one.py",)) -> DevelopmentPlanCandidate:
    parent = _parent()
    children = tuple(
        ChildTaskProposal(
            child_key=str(index),
            objective=f"implement bounded change {index}",
            task_type=TaskType.WORKER,
            required_capabilities=("coding",),
        )
        for index, _path in enumerate(paths, start=1)
    )
    proposal = RootPlanningProposal(
        parent_task_id=parent.task_id,
        rationale="bounded parallel implementation proposal",
        children=children,
    )
    return DevelopmentPlanningBridge(tmp_path).build_candidate(
        parent,
        proposal,
        run_id="phase8-multirole-contract",
        base_revision="abc123",
        task_specs={
            child.child_key: _worker_spec(path)
            for child, path in zip(children, paths)
        },
    )


def _peer(
    *,
    instance_id: str = "Agent:free-3.v2",
    lease_until: str = "2026-09-16T13:00:00+00:00",
) -> PeerRecord:
    timestamp = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc).isoformat()
    return PeerRecord(
        role="agent",
        instance_id=instance_id,
        generation=1,
        pid=1234,
        revision="029d34986cb297b4865637b353063683d103a1ad",
        started_at=timestamp,
        heartbeat_at=timestamp,
        lease_until=lease_until,
        status=PeerStatus.READY,
        capabilities=("role", "task-ownership"),
    )


def _implementer_projection(candidate: DevelopmentPlanCandidate) -> tuple[RoleTaskAssignment, dict[str, Task]]:
    task = candidate.plan["tasks"][0]
    task_id = task["task_id"]
    assignment = RoleTaskAssignment(
        task_id=task_id,
        role_id="implementer",
        instance_id="Agent:free-3.v2",
        generation=1,
        owned_paths=tuple(task["ownership"]),
    )
    profile = Task(
        task_id=task_id,
        objective="implement bounded change",
        task_type=TaskType.WORKER,
        required_capabilities=["coding"],
        risk=RiskLevel.NORMAL,
        sensitivity="normal",
    )
    return assignment, {task_id: profile}


def test_multirole_adapter_projects_roles_without_persisting_or_selecting_provider(tmp_path):
    from scripts.devfarm_multirole import MultiRolePlanAdapter

    candidate = _candidate(tmp_path)
    assignment, profiles = _implementer_projection(candidate)
    peer = _peer()
    manifests = builtin_role_manifests()
    instance = RoleInstance.from_peer(manifests["implementer"], peer)

    projected = MultiRolePlanAdapter(tmp_path).build_candidate(
        candidate,
        role_manifests=manifests,
        role_instances={instance.instance_id: instance},
        task_profiles=profiles,
        assignments=(assignment,),
        peers=(peer,),
        now="2026-09-16T12:00:00+00:00",
    )

    task = projected.plan["tasks"][0]
    assert task["role_id"] == "implementer"
    assert task["role_instance_id"] == "Agent:free-3.v2"
    assert task["role_generation"] == 1
    assert task["assignment"]["provider_id"] == "gemini"
    assert projected.role_assignments == (assignment,)
    assert projected.role_manifests["implementer"] == manifests["implementer"].to_dict()
    assert not (tmp_path / ".devfarm").exists()


def test_multirole_adapter_requires_all_plan_tasks_to_have_role_assignments(tmp_path):
    from scripts.devfarm_multirole import MultiRolePlanAdapter

    candidate = _candidate(tmp_path, ("src/one.py", "src/two.py"))
    assignment, profiles = _implementer_projection(candidate)
    peer = _peer()
    manifests = builtin_role_manifests()
    instance = RoleInstance.from_peer(manifests["implementer"], peer)

    with pytest.raises(RoleAssignmentError, match="exactly one role assignment"):
        MultiRolePlanAdapter(tmp_path).build_candidate(
            candidate,
            role_manifests=manifests,
            role_instances={instance.instance_id: instance},
            task_profiles=profiles,
            assignments=(assignment,),
            peers=(peer,),
            now="2026-09-16T12:00:00+00:00",
        )


def test_multirole_adapter_rejects_expired_role_instance_before_plan_projection(tmp_path):
    from scripts.devfarm_multirole import MultiRolePlanAdapter

    candidate = _candidate(tmp_path)
    assignment, profiles = _implementer_projection(candidate)
    peer = _peer(lease_until="2026-09-16T11:59:59+00:00")
    manifests = builtin_role_manifests()
    instance = RoleInstance.from_peer(manifests["implementer"], peer)

    with pytest.raises(RoleAssignmentError, match="lease is not live"):
        MultiRolePlanAdapter(tmp_path).build_candidate(
            candidate,
            role_manifests=manifests,
            role_instances={instance.instance_id: instance},
            task_profiles=profiles,
            assignments=(assignment,),
            peers=(peer,),
            now="2026-09-16T12:00:00+00:00",
        )


def test_multirole_adapter_does_not_accept_role_provider_or_authority_fields(tmp_path):
    from scripts.devfarm_multirole import MultiRolePlanAdapter

    candidate = _candidate(tmp_path)
    assignment, profiles = _implementer_projection(candidate)
    peer = _peer()
    manifests = builtin_role_manifests()
    instance = RoleInstance.from_peer(manifests["implementer"], peer)

    projected = MultiRolePlanAdapter(tmp_path).build_candidate(
        candidate,
        role_manifests=manifests,
        role_instances={instance.instance_id: instance},
        task_profiles=profiles,
        assignments=(assignment,),
        peers=(peer,),
        now="2026-09-16T12:00:00+00:00",
    )
    role_record = projected.to_dict()["role_assignments"][0]
    assert "provider_id" not in role_record
    assert "approval" not in role_record
    assert "integration" not in role_record


def test_commander_plan_role_identity_is_atomic_and_bounded(tmp_path):
    candidate = _candidate(tmp_path)
    task = candidate.plan["tasks"][0]
    incomplete = {
        **candidate.plan,
        "tasks": [{**task, "role_id": "implementer"}],
    }

    with pytest.raises(DevFarmError, match="provided together"):
        validate_plan(incomplete)

    invalid_generation = {
        **candidate.plan,
        "tasks": [{
            **task,
            "role_id": "implementer",
            "role_instance_id": "Agent:free-3.v2",
            "role_generation": 0,
        }],
    }
    with pytest.raises(DevFarmError, match="role_generation"):
        validate_plan(invalid_generation)
