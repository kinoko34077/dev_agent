from __future__ import annotations

from pathlib import Path

from scripts.devfarm import write_manifest
from scripts.devfarm_orchestrator import (
    DevFarmOrchestrator,
    HostConcurrencyGovernor,
    RemoteConcurrencyGovernor,
)
from scripts.devfarm_production_composition import (
    Phase8ProductionComposition,
    Phase8ProductionSubmission,
)
from src.dev_agent.domain.protocol import Task, TaskType
from src.dev_agent.operation import OperationConfig
from src.dev_agent.intelligence.planner import (
    ChildTaskProposal,
    PlannerDependencyType,
    RootPlanningProposal,
)
from tests.v2.test_devfarm_commander import _WorkerProvider, _patch, _repo


def test_phase8_submission_boundary_is_exported_for_one_bounded_root():
    assert Phase8ProductionSubmission is not None


def test_phase8_public_driver_only_submits_and_observes(tmp_path: Path):
    repository, targets, revision = _repo(tmp_path)
    config = OperationConfig(
        data_dir=tmp_path / "operation-state",
        provider_id="fake",
        model="deterministic",
        worker_id="phase8-submission-boundary",
        idle_sleep_seconds=0.01,
    )

    def planner(root: Task) -> RootPlanningProposal:
        return RootPlanningProposal(
            parent_task_id=root.task_id,
            proposal_id="phase8-submission-proposal",
            rationale="two independent workers and one continuation",
            children=(
                ChildTaskProposal(
                    child_key="worker-a",
                    objective="add the harmless return to worker A",
                    task_type=TaskType.WORKER,
                    required_capabilities=("coding",),
                ),
                ChildTaskProposal(
                    child_key="worker-b",
                    objective="add the harmless return to worker B",
                    task_type=TaskType.WORKER,
                    required_capabilities=("coding",),
                ),
                ChildTaskProposal(
                    child_key="continuation",
                    objective="continue after both integrations",
                    task_type=TaskType.DETERMINISTIC,
                    suggested_owner="codex",
                    dependencies=("worker-a", "worker-b"),
                    dependency_types={
                        "worker-a": PlannerDependencyType.CODE_INTEGRATED,
                        "worker-b": PlannerDependencyType.CODE_INTEGRATED,
                    },
                ),
            ),
        )

    def worker_spec(path: str) -> dict[str, object]:
        return {
            "manifest": {
                "task_type": "worker",
                "allowed_files": [path],
                "read_files": [path, "tests/v2/baseline.py"],
                "forbidden_files": [],
                "external_provider_allowed": True,
                "approved_provider_ids": ["cloudflare"],
                "outbound_files": [path],
                "requirements": ["keep the change narrow"],
                "acceptance": ["the focused test passes"],
                "test_commands": [f"python -m pytest {path} tests/v2/baseline.py -q"],
                "max_attempts": 2,
                "output_contract": {},
            },
            "assignment": {
                "provider_id": "cloudflare",
                "provider_binding_id": "cloudflare",
                "model_id": "@cf/meta/llama-3.1-8b-instruct",
            },
        }

    def task_specs(_root: Task, _proposal: RootPlanningProposal):
        return {
            "worker-a": worker_spec(targets[0]),
            "worker-b": worker_spec(targets[1]),
            "continuation": {
                "owner": "codex",
                "codex_direct_reason": "continuation is released by Operation after integration",
            },
        }

    def providers(bindings):
        return {
            bindings["worker-a"]: _WorkerProvider(
                {
                    "status": "completed",
                    "changed_files": [targets[0]],
                    "tests_run": [],
                    "tests_passed": True,
                    "known_issues": [],
                    "assumptions": [],
                    "patch": _patch(targets[0]),
                    "notes": "bounded worker A",
                }
            ),
            bindings["worker-b"]: _WorkerProvider(
                {
                    "status": "completed",
                    "changed_files": [targets[1]],
                    "tests_run": [],
                    "tests_passed": True,
                    "known_issues": [],
                    "assumptions": [],
                    "patch": _patch(targets[1]),
                    "notes": "bounded worker B",
                }
            ),
        }

    submission = Phase8ProductionSubmission(
        operation_config=config,
        repository=repository,
        planner=planner,
        task_specs=task_specs,
        providers=providers,
        orchestrator=DevFarmOrchestrator(
            remote_governor=RemoteConcurrencyGovernor(max_inflight=2),
            host_governor=HostConcurrencyGovernor(worktree_verification_slots=1),
            verification_trust_level="TRUSTED_HOST_EXEC",
            operator_approved=True,
        ),
        review_proposal=lambda packet: {
            "decision": "APPROVE_INTEGRATION",
            "findings": [],
            "evidence_refs": [{"kind": "bounded-review", "task_id": packet["task_id"]}],
        },
        final_review_decision=lambda _packet, proposal: proposal,
        target_checkout=repository,
    )
    composition = Phase8ProductionComposition(
        submit_boundary=submission.submit,
        observe_boundary=submission.observe,
    )

    # The public driver performs only these two calls.  It does not know the
    # internal Planner/Worker/Verification/Review/Integration sequence.
    result = composition.submit("run one bounded production root")
    observed = composition.observe(result["run_id"])

    assert result["execution"]["integrated"] == ["worker-a", "worker-b"]
    assert result["execution"]["continuation_ready"] is True
    assert {item["status"] for item in observed["workers"]} == {"INTEGRATED"}
