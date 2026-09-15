from __future__ import annotations

from datetime import datetime, timezone
import threading
from uuid import uuid4

import pytest

from scripts.devfarm_planning_bridge import DevelopmentPlanCandidate, DevelopmentPlanningBridge
from scripts.devfarm_commander import create_plan
from scripts.devfarm_plan_validation import validate_plan
from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_repository import read_json
from scripts.devfarm_orchestrator import (
    DevFarmOrchestrator,
    HostConcurrencyGovernor,
    RemoteConcurrencyGovernor,
)
from scripts.devfarm_multirole import MultiRolePlanAdapter
from scripts.devfarm_supervisor import CodexSupervisedCommanderRun
from scripts.devfarm_commander import mark_integrated
from src.dev_agent.coordination.protocol import PeerRecord, PeerStatus
from src.dev_agent.domain.protocol import IntelligenceTier, RiskLevel, Task, TaskType
from src.dev_agent.intelligence.planner import ChildTaskProposal, RootPlanningProposal
from src.dev_agent.intelligence.role_manifest import (
    RoleAssignmentError,
    RoleInstance,
    RoleTaskAssignment,
    RoleTaskProfile,
    builtin_role_manifests,
)
from tests.v2.test_devfarm_commander import (
    _WorkerProvider,
    _git,
    _integrate_codex_review,
    _manifest,
    _patch,
    _repo,
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


class _BarrierWorkerProvider(_WorkerProvider):
    def __init__(self, output: object, barrier: threading.Barrier) -> None:
        super().__init__(output)
        self._barrier = barrier

    def request(self, request):
        self._barrier.wait(timeout=10)
        return super().request(request)


def test_stage5_existing_commander_runs_two_role_instances_through_review_and_integration(tmp_path):
    root, targets, revision = _repo(tmp_path)
    manifest_paths = [
        _manifest(root, revision, f"worker-{letter}", target)
        for letter, target in zip(("a", "b"), targets)
    ]
    manifest_values = [read_json(path) for path in manifest_paths]
    worker_tasks = [
        {
            "task_id": f"worker-{letter}",
            "owner": "worker",
            "manifest_path": f".devfarm/tasks/worker-{letter}.json",
            "ownership": [target],
            "role_id": "implementer",
            "role_instance_id": f"Agent:worker-{letter}.v1",
            "role_generation": 1,
            "assignment": {
                "provider_id": "cloudflare",
                "provider_binding_id": "cloudflare",
                "model_id": "@cf/meta/llama-3.1-8b-instruct",
            },
        }
        for letter, target in zip(("a", "b"), targets)
    ]
    reviewer_task = {
        "task_id": "role-reviewer",
        "owner": "codex",
        "task_type": "reasoning",
        "dependencies": ["worker-a", "worker-b"],
        "dependency_types": {
            "worker-a": "CODE_INTEGRATED",
            "worker-b": "CODE_INTEGRATED",
        },
        "ownership": ["docs/commander-review.md"],
        "role_id": "reviewer",
        "role_instance_id": "Codex:reviewer.v1",
        "role_generation": 1,
    }
    base_candidate = DevelopmentPlanCandidate(
        plan={
            "run_id": "phase8-stage5-runtime",
            "objective": "run two independent implementers and a separate reviewer",
            "base_revision": revision,
            "tasks": [*worker_tasks, reviewer_task],
        },
        manifests=tuple(
            (f".devfarm/tasks/worker-{letter}.json", manifest)
            for letter, manifest in zip(("a", "b"), manifest_values)
        ),
    )
    peers = tuple(
        _peer(instance_id=f"Agent:worker-{letter}.v1")
        for letter in ("a", "b")
    ) + (
        _peer(instance_id="Codex:reviewer.v1"),
    )
    manifests = builtin_role_manifests()
    instances = {
        peer.instance_id: RoleInstance.from_peer(
            manifests["implementer" if peer.instance_id.startswith("Agent:") else "reviewer"],
            peer,
        )
        for peer in peers
    }
    profiles = {
        "worker-a": RoleTaskProfile(
            task_id="worker-a",
            task_type=TaskType.WORKER,
            required_capabilities=["coding"],
            intelligence_tier=IntelligenceTier.L1,
            risk=RiskLevel.NORMAL,
            sensitivity="normal",
        ),
        "worker-b": RoleTaskProfile(
            task_id="worker-b",
            task_type=TaskType.WORKER,
            required_capabilities=["coding"],
            intelligence_tier=IntelligenceTier.L1,
            risk=RiskLevel.NORMAL,
            sensitivity="normal",
        ),
        "role-reviewer": RoleTaskProfile(
            task_id="role-reviewer",
            task_type=TaskType.REASONING,
            required_capabilities=["review"],
            intelligence_tier=IntelligenceTier.L2,
            risk=RiskLevel.NORMAL,
            sensitivity="normal",
        ),
    }
    assignments = (
        RoleTaskAssignment("worker-a", "implementer", "Agent:worker-a.v1", 1, (targets[0],)),
        RoleTaskAssignment("worker-b", "implementer", "Agent:worker-b.v1", 1, (targets[1],)),
        RoleTaskAssignment(
            "role-reviewer",
            "reviewer",
            "Codex:reviewer.v1",
            1,
            ("docs/commander-review.md",),
        ),
    )
    projected = MultiRolePlanAdapter(root).build_candidate(
        base_candidate,
        role_manifests=manifests,
        role_instances=instances,
        task_profiles=profiles,
        assignments=assignments,
        peers=peers,
        now="2026-09-16T12:00:00+00:00",
    )
    created = create_plan(root, projected.plan)
    assert [task["role_id"] for task in created["tasks"]] == [
        "implementer",
        "implementer",
        "reviewer",
    ]

    barrier = threading.Barrier(2)
    provider_output = lambda target: {
        "status": "completed",
        "changed_files": [target],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch(target),
        "notes": "bounded stage5 proposal",
    }
    providers = {
        "worker-a": _BarrierWorkerProvider(provider_output(targets[0]), barrier),
        "worker-b": _BarrierWorkerProvider(provider_output(targets[1]), barrier),
    }
    orchestrator = DevFarmOrchestrator(
        remote_governor=RemoteConcurrencyGovernor(max_inflight=2),
        host_governor=HostConcurrencyGovernor(worktree_verification_slots=1),
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )
    runner = CodexSupervisedCommanderRun(root, "phase8-stage5-runtime")
    runner.create(roadmap_reference={"work_address": "5-B-1"})
    review_step = runner.advance(
        providers=providers,
        orchestrator=orchestrator,
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )
    assert review_step.status == "REVIEWING"
    assert review_step.metrics["worker_dispatch_count"] == 2
    assert len(review_step.review_packets) == 2
    assert orchestrator.remote_governor.snapshot()["peak"] == 2
    assert {task["status"] for task in runner.plan()["tasks"][:2]} == {"HOST_VERIFIED"}
    assert [task["role_instance_id"] for task in runner.plan()["tasks"][:2]] == [
        "Agent:worker-a.v1",
        "Agent:worker-b.v1",
    ]

    for task_id in ("worker-a", "worker-b"):
        task = runner.plan()["tasks"][[item["task_id"] for item in runner.plan()["tasks"]].index(task_id)]
        runner.record_review_decision(
            task_id,
            attempt_id=task["last_attempt_id"],
            decision="APPROVE_INTEGRATION",
            evidence_refs=[{"kind": "stage5-verification", "path": task["result_ref"]}],
        )
        decision_id = runner.plan()["review_decisions"][-1]["decision_id"]
        runner.integrate_approved_worker(
            task_id,
            decision_id=decision_id,
            commit_message=f"integrate {task_id}",
            target_checkout=root,
            target_ref="HEAD",
        )

    after_workers = runner.plan()
    assert after_workers["tasks"][2]["status"] == "READY"
    reviewer_revision, reviewer_digest = _integrate_codex_review(root)
    mark_integrated(
        root,
        "phase8-stage5-runtime",
        "role-reviewer",
        note="separate reviewer proposal was accepted by Host",
        target_ref="HEAD",
        integration_revision=reviewer_revision,
        source_attempt_id="role-reviewer",
        verified_patch_digest=reviewer_digest,
    )
    final = runner.plan()
    assert final["status"] == "INTEGRATED"
    assert final["tasks"][0]["status"] == "INTEGRATED"
    assert final["tasks"][1]["status"] == "INTEGRATED"
    assert final["tasks"][2]["status"] == "INTEGRATED"
