from __future__ import annotations

from pathlib import Path

import pytest

from scripts.devfarm import write_manifest
from scripts.devfarm_orchestrator import (
    DevFarmOrchestrator,
    HostConcurrencyGovernor,
    RemoteConcurrencyGovernor,
)
from scripts.devfarm_production_composition import (
    Phase8ProductionComposition,
    ProductionCompositionError,
    Phase8ProductionSubmission,
    _bounded_projection,
    _cleanup_projection_fields,
)
from src.dev_agent.domain.protocol import Task, TaskStatus, TaskType
from src.dev_agent.operation import OperationConfig, OperationService
from src.dev_agent.intelligence.planner import (
    ChildTaskProposal,
    PlannerDependencyType,
    RootPlanningProposal,
)
from tests.v2.test_devfarm_commander import _WorkerProvider, _patch, _repo


class _LifecycleProbe:
    def __init__(self, unloaded=(), error=None):
        self.unloaded = tuple(unloaded)
        self.error = error
        self.calls = 0

    def unload_idle(self, *, deadline_seconds=30.0):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.unloaded


class _LocalProviderProbe:
    provider_id = "ollama"
    model = "qwen3.5:9b"

    def __init__(self, manager):
        self.model_manager = manager


def test_phase8_submission_boundary_is_exported_for_one_bounded_root():
    assert Phase8ProductionSubmission is not None


def test_phase8_terminal_cleanup_unloads_each_local_model_manager_once():
    manager = _LifecycleProbe(unloaded=("qwen3.5:9b",))
    provider = _LocalProviderProbe(manager)

    cleanup = Phase8ProductionSubmission._unload_local_provider_models(
        {"worker-a": provider},
        {"worker-b": provider},
    )

    assert cleanup == {
        "unloaded_models": ["qwen3.5:9b"],
        "errors": [],
    }
    assert manager.calls == 1


def test_phase8_terminal_cleanup_accepts_independent_reviewer_manager():
    worker_manager = _LifecycleProbe(unloaded=("qwen3.5:9b",))
    reviewer_manager = _LifecycleProbe(unloaded=("gemma4:12b",))
    worker = _LocalProviderProbe(worker_manager)
    reviewer = _LocalProviderProbe(reviewer_manager)
    reviewer.model = "gemma4:12b"

    cleanup = Phase8ProductionSubmission._unload_local_provider_models(
        {"worker-a": worker},
        {"reviewer": reviewer},
    )

    assert cleanup == {
        "unloaded_models": ["gemma4:12b", "qwen3.5:9b"],
        "errors": [],
    }
    assert worker_manager.calls == 1
    assert reviewer_manager.calls == 1


def test_phase8_cleanup_projection_flattens_errors_for_submission_boundary():
    fields = _cleanup_projection_fields(
        {
            "unloaded_models": ["qwen3.5:9b"],
            "errors": [{"model": "gemma4:12b", "category": "lifecycle_failure"}],
        }
    )

    assert fields == (
        ["qwen3.5:9b"],
        ["gemma4:12b:lifecycle_failure"],
    )
    assert _bounded_projection(
        {
            "execution": {
                "local_model_unloaded_models": fields[0],
                "local_model_cleanup_errors": fields[1],
            }
        }
    ) == {
        "execution": {
            "local_model_unloaded_models": ["qwen3.5:9b"],
            "local_model_cleanup_errors": ["gemma4:12b:lifecycle_failure"],
        }
    }


def test_phase8_submission_keeps_reviewer_provider_inventory_for_terminal_cleanup():
    reviewer = object()
    submission = Phase8ProductionSubmission(
        operation_config=object(),
        repository=".",
        planner=lambda _root: None,
        task_specs=lambda _root, _proposal: {},
        providers=lambda _bindings: {},
        orchestrator=object(),
        review_proposal=lambda _packet: {},
        final_review_decision=lambda _packet, _proposal: {},
        target_checkout=".",
        reviewer_providers={"reviewer": reviewer},
    )

    assert submission.reviewer_providers == {"reviewer": reviewer}


def test_phase8_submission_keeps_planner_provider_inventory_for_failure_cleanup():
    planner = object()
    submission = Phase8ProductionSubmission(
        operation_config=object(),
        repository=".",
        planner=lambda _root: None,
        task_specs=lambda _root, _proposal: {},
        providers=lambda _bindings: {},
        orchestrator=object(),
        review_proposal=lambda _packet: {},
        final_review_decision=lambda _packet, _proposal: {},
        target_checkout=".",
        planner_providers={"planner": planner},
    )

    assert submission.planner_providers == {"planner": planner}


def test_phase8_submission_does_not_mask_cleanup_boundary_failure(monkeypatch):
    submission = Phase8ProductionSubmission(
        operation_config=object(),
        repository=".",
        planner=lambda _root: None,
        task_specs=lambda _root, _proposal: {},
        providers=lambda _bindings: {},
        orchestrator=object(),
        review_proposal=lambda _packet: {},
        final_review_decision=lambda _packet, _proposal: {},
        target_checkout=".",
    )

    monkeypatch.setattr(
        submission,
        "_submit_without_cleanup",
        lambda _objective, **_kwargs: {"execution": {}},
    )

    def fail_cleanup(*_provider_maps):
        raise RuntimeError("synthetic cleanup boundary failure")

    monkeypatch.setattr(submission, "_unload_local_provider_models", fail_cleanup)

    result = submission.submit("bounded cleanup projection")

    assert result["execution"]["local_model_cleanup_errors"] == ["unknown:cleanup_failure"]
    assert result["execution"]["local_model_unloaded_models"] == []


def test_phase8_submission_cleans_up_local_models_when_execution_fails(tmp_path: Path, monkeypatch):
    repository, targets, _revision = _repo(tmp_path)
    config = OperationConfig(
        data_dir=tmp_path / "operation-state",
        provider_id="fake",
        model="deterministic",
        worker_id="phase8-failure-cleanup",
        idle_sleep_seconds=0.01,
    )

    def planner(root: Task) -> RootPlanningProposal:
        return RootPlanningProposal(
            parent_task_id=root.task_id,
            proposal_id="phase8-failure-cleanup-proposal",
            rationale="two workers and a continuation",
            children=(
                ChildTaskProposal(
                    child_key="worker-a",
                    objective="bounded worker A",
                    task_type=TaskType.WORKER,
                ),
                ChildTaskProposal(
                    child_key="worker-b",
                    objective="bounded worker B",
                    task_type=TaskType.WORKER,
                ),
                ChildTaskProposal(
                    child_key="continuation",
                    objective="continue after integration",
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
                "read_files": [path],
                "forbidden_files": [],
                "external_provider_allowed": True,
                "approved_provider_ids": ["ollama"],
                "outbound_files": [path],
                "requirements": ["keep the change narrow"],
                "acceptance": ["the focused test passes"],
                "test_commands": [f"python -m pytest {path} -q"],
                "max_attempts": 1,
                "output_contract": {},
            },
            "assignment": {
                "provider_id": "ollama",
                "provider_binding_id": "ollama:local:qwen3.5-9b",
                "model_id": "qwen3.5:9b",
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

    planner_manager = _LifecycleProbe(unloaded=("qwen3.5:9b",))
    manager = _LifecycleProbe(unloaded=("qwen3.5:9b",), error=RuntimeError("cleanup failed"))

    def providers(bindings):
        return {task_id: _LocalProviderProbe(manager) for task_id in bindings.values()}

    planner_provider = _LocalProviderProbe(planner_manager)

    def fail_before_terminal_cleanup(_self):
        raise RuntimeError("synthetic production execution failure")

    monkeypatch.setattr(
        "scripts.devfarm_production_composition.Phase8ProductionExecutor.advance",
        fail_before_terminal_cleanup,
    )
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
        review_proposal=lambda _packet: {"decision": "APPROVE_INTEGRATION"},
        final_review_decision=lambda _packet, proposal: proposal,
        target_checkout=repository,
        planner_providers={"planner": planner_provider},
        local_trial=True,
    )

    with pytest.raises(RuntimeError, match="synthetic production execution failure"):
        submission.submit("run one failing production root")

    assert manager.calls == 1
    assert planner_manager.calls == 1


def test_phase8_shape_preflight_rejects_dependent_worker_before_handoff():
    proposal = RootPlanningProposal(
        parent_task_id="root",
        proposal_id="phase8-shape-preflight",
        rationale="bounded phase 8 shape",
        children=(
            ChildTaskProposal(
                child_key="worker-a",
                objective="implement A",
                task_type=TaskType.WORKER,
            ),
            ChildTaskProposal(
                child_key="worker-b",
                objective="implement B",
                task_type=TaskType.WORKER,
                dependencies=("worker-a",),
            ),
            ChildTaskProposal(
                child_key="continuation",
                objective="continue after integration",
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

    with pytest.raises(ProductionCompositionError, match="independent"):
        Phase8ProductionSubmission.validate_phase8_root_shape(proposal)


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
        local_trial=True,
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
    assert result["execution"]["continuation_state"] == TaskStatus.COMPLETED.value
    assert result["execution"]["root_state"] == TaskStatus.COMPLETED.value
    assert {item["status"] for item in observed["workers"]} == {"INTEGRATED"}
    root_status = OperationService.read_status(config, result["root_task_id"])
    assert root_status["state"] == TaskStatus.COMPLETED.value
    assert root_status["claim_count"] == 0
    continuation_status = OperationService.read_status(config, result["execution"]["continuation_task_id"])
    assert continuation_status["state"] == TaskStatus.COMPLETED.value
    assert continuation_status["execution_attempts"] == 1
