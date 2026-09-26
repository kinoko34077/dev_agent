from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts.devfarm import write_manifest
from scripts.devfarm_commander import create_plan
from scripts.devfarm_orchestrator import (
    DevFarmOrchestrator,
    HostConcurrencyGovernor,
    RemoteConcurrencyGovernor,
)
from scripts.devfarm_planning_bridge import DevelopmentPlanningBridge
from scripts.devfarm_supervisor import CodexSupervisedCommanderRun
from src.dev_agent.domain.protocol import Task, TaskStatus, TaskType
from src.dev_agent.intelligence.planner import (
    ChildTaskProposal,
    PlannerDependencyType,
    RootPlanningProposal,
)
from src.dev_agent.operation import OperationConfig, OperationService
from tests.v2.test_devfarm_commander import _WorkerProvider, _patch, _repo


_REVISION_A = "a" * 40
_REVISION_B = "b" * 40
_DIGEST_A = "1" * 64
_DIGEST_B = "2" * 64


class _FakeCommander:
    def __init__(self) -> None:
        self.advance_calls = 0
        self.review_calls: list[dict[str, object]] = []
        self.integration_calls: list[str] = []
        self._plan = {
            "run_id": "devfarm-run",
            "status": "READY",
            "tasks": [
                {
                    "task_id": "devfarm-a",
                    "planning_proposal_id": "proposal-1",
                    "planner_child_key": "worker-a",
                    "owner": "worker",
                    "status": "READY",
                    "last_attempt_id": None,
                },
                {
                    "task_id": "devfarm-b",
                    "planning_proposal_id": "proposal-1",
                    "planner_child_key": "worker-b",
                    "owner": "worker",
                    "status": "READY",
                    "last_attempt_id": None,
                },
            ],
            "review_decisions": [],
        }

    def plan(self):
        return self._plan

    def status(self):
        return SimpleNamespace(status=self._plan["status"])

    def advance(self, **kwargs):
        self.advance_calls += 1
        self._plan["status"] = "REVIEWING"
        for index, task in enumerate(self._plan["tasks"]):
            if task.get("status") == "INTEGRATED":
                continue
            task.update(
                status="HOST_VERIFIED",
                last_attempt_id=f"attempt-{index}",
                verified_patch_digest=_DIGEST_A if index == 0 else _DIGEST_B,
            )
        return SimpleNamespace(status="REVIEWING")

    def review_packet(self, task_id, *, attempt_id):
        return {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "review_packet": "bounded",
        }

    def record_review_decision(self, task_id, **kwargs):
        decision = {
            "decision_id": f"decision-{task_id}",
            "task_id": task_id,
            "attempt_id": kwargs["attempt_id"],
            "decision": kwargs["decision"],
        }
        self.review_calls.append(decision)
        self._plan["review_decisions"].append(decision)
        return SimpleNamespace(status="INTEGRATING")

    def integrate_approved_worker(self, task_id, **kwargs):
        self.integration_calls.append(task_id)
        task = next(item for item in self._plan["tasks"] if item["task_id"] == task_id)
        task.update(
            status="INTEGRATED",
            integration_revision=_REVISION_A if task_id.endswith("a") else _REVISION_B,
            source_attempt_id=task["last_attempt_id"],
        )
        return SimpleNamespace(status="ACTIVE")


class _FakeOperation:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def record_devfarm_integration_evidence(self, **kwargs):
        self.calls.append(dict(kwargs))
        return (
            Task(
                task_id=str(uuid4()),
                objective="integrated worker",
                task_type=TaskType.WORKER,
                status=TaskStatus.COMPLETED,
            ),
            Task(
                task_id=str(uuid4()),
                objective="dependent continuation",
                task_type=TaskType.DETERMINISTIC,
                status=TaskStatus.QUEUED,
            ),
        )


class _LifecycleProbe:
    def __init__(self, unloaded=()):
        self.unloaded = tuple(unloaded)
        self.calls = 0

    def unload_idle(self, *, deadline_seconds=30.0):
        self.calls += 1
        return self.unloaded


class _LocalProviderProbe:
    def __init__(self, manager, model):
        self.model_manager = manager
        self.model = model


def _executor(**overrides):
    from scripts.devfarm_production_composition import Phase8ProductionExecutor

    values = {
        "operation": _FakeOperation(),
        "commander": _FakeCommander(),
        "proposal_id": "proposal-1",
        "devfarm_run_id": "devfarm-run",
        "bindings": {
            "worker-a": "devfarm-a",
            "worker-b": "devfarm-b",
        },
        "providers": {"devfarm-a": object(), "devfarm-b": object()},
        "orchestrator": object(),
        "review_proposal": lambda packet: {
            "decision": "APPROVE_INTEGRATION",
            "findings": [],
            "evidence_refs": [{"kind": "bounded-review"}],
        },
        "final_review_decision": lambda packet, proposal: proposal,
        "target_checkout": ".",
    }
    values.update(overrides)
    return Phase8ProductionExecutor(**values), values


def test_phase8_executor_runs_one_bounded_worker_review_integration_pass():
    executor, values = _executor()

    result = executor.advance()

    assert result["run_id"] == "devfarm-run"
    assert result["integrated"] == ["worker-a", "worker-b"]
    assert result["continuation_ready"] is True
    assert values["commander"].advance_calls == 1
    assert values["commander"].integration_calls == ["devfarm-a", "devfarm-b"]
    assert [call["child_key"] for call in values["operation"].calls] == ["worker-a", "worker-b"]
    assert all(call["status"] == "INTEGRATED" for call in values["operation"].calls)


def test_phase8_executor_resume_does_not_reintegrate_completed_workers():
    executor, values = _executor()

    first = executor.advance()
    second = executor.advance()

    assert first["integrated"] == ["worker-a", "worker-b"]
    assert second["integrated"] == []
    assert values["commander"].integration_calls == ["devfarm-a", "devfarm-b"]
    assert len(values["operation"].calls) == 2


def test_phase8_executor_does_not_treat_reviewer_proposal_as_final_approval():
    executor, values = _executor(
        final_review_decision=lambda packet, proposal: {
            "decision": "REJECT",
            "findings": ["final authority rejected the proposal"],
            "evidence_refs": [],
        }
    )

    result = executor.advance()

    assert result["integrated"] == []
    assert values["commander"].integration_calls == []
    assert values["operation"].calls == []


def test_phase8_executor_releases_worker_models_before_local_reviewer():
    worker_manager = _LifecycleProbe(("qwen3.5:9b",))
    reviewer_manager = _LifecycleProbe(("gemma4:12b",))
    seen: list[tuple[int, int]] = []

    def review_proposal(packet):
        seen.append((worker_manager.calls, reviewer_manager.calls))
        return {
            "decision": "APPROVE_INTEGRATION",
            "findings": [],
            "evidence_refs": [{"kind": "bounded-review"}],
        }

    executor, values = _executor(
        providers={
            "devfarm-a": _LocalProviderProbe(worker_manager, "qwen3.5:9b"),
            "devfarm-b": _LocalProviderProbe(worker_manager, "qwen3.5:9b"),
        },
        reviewer_providers={
            "reviewer": _LocalProviderProbe(reviewer_manager, "gemma4:12b"),
        },
        review_proposal=review_proposal,
    )

    result = executor.advance()

    assert result["integrated"] == ["worker-a", "worker-b"]
    assert seen == [(1, 0), (1, 0)]
    assert result["reviewer_model_switch"] == {"unloaded_models": ["qwen3.5:9b"], "errors": []}
    assert reviewer_manager.calls == 0


def test_phase8_executor_rebinds_latest_worker_failure_to_distinct_local_model(monkeypatch, tmp_path):
    import scripts.devfarm_production_composition as composition

    commander = _FakeCommander()
    commander.root = tmp_path
    failed = commander._plan["tasks"][0]
    failed.update(
        status="REJECTED",
        block_reason="proposal_failed",
        last_attempt_id="qwen-attempt-2",
        result_ref=".devfarm/results/qwen-attempt-2.json",
        attempt_count=2,
        max_attempts=3,
        manifest_history=[".devfarm/manifests/worker-a.json"],
        manifest_path=".devfarm/manifests/worker-a-rework.json",
        last_error="WORKER_OUTPUT_INVALID_JSON: worker response JSON is invalid",
    )
    manifest = {"allowed_files": ["src/worker_a.py"], "outbound_files": ["src/worker_a.py"]}
    monkeypatch.setattr(
        composition,
        "load_worker_manifest",
        lambda _root, _task: (Path("worker-a.json"), manifest),
    )
    handoff_calls: list[dict[str, object]] = []
    reassign_calls: list[dict[str, object]] = []

    def rework_handoff(task_id, **kwargs):
        handoff_calls.append({"task_id": task_id, **kwargs})
        return {"kind": "repair_request", "task_id": task_id}

    def reassign(task_id, **kwargs):
        reassign_calls.append({"task_id": task_id, **kwargs})

    commander.rework_handoff = rework_handoff
    commander.reassign = reassign
    primary = SimpleNamespace(
        provider_id="ollama",
        model_id="qwen3.5:9b",
        provider_binding_id="ollama:local:qwen3.5-9b",
    )
    fallback = SimpleNamespace(
        provider_id="ollama",
        model_id="gemma4:12b",
        provider_binding_id="ollama:local:gemma4-12b",
    )
    executor, values = _executor(
        commander=commander,
        providers={"devfarm-a": primary, "devfarm-b": object()},
        fallback_providers={"devfarm-a": fallback},
    )

    result = executor._fallback_failed_proposals(commander.plan())

    assert result == ["worker-a"]
    assert values["commander"] is commander
    assert executor.providers["devfarm-a"] is fallback
    assert handoff_calls[0]["repair_context"]["directive_rebound"] is True
    assert handoff_calls[0]["repair_context"]["fallback_to_provider"] == "ollama"
    assert reassign_calls[0]["provider_binding_id"] == "ollama:local:gemma4-12b"
    assert reassign_calls[0]["model_id"] == "gemma4:12b"


def test_phase8_executor_rejects_incomplete_identity_before_dispatch():
    executor, values = _executor(bindings={"worker-a": "devfarm-a"})

    with pytest.raises(ValueError, match="exact worker identity"):
        executor.advance()

    assert values["commander"].advance_calls == 0


def test_phase8_executor_reordered_commander_tasks_use_identity_not_position():
    executor, values = _executor()
    values["commander"]._plan["tasks"][:] = list(reversed(values["commander"]._plan["tasks"]))

    result = executor.advance()

    assert result["integrated"] == ["worker-a", "worker-b"]
    assert values["commander"].integration_calls == ["devfarm-a", "devfarm-b"]
    assert [call["child_key"] for call in values["operation"].calls] == ["worker-a", "worker-b"]


def test_phase8_executor_composes_real_operation_and_commander_boundaries(tmp_path):
    from scripts.devfarm_production_composition import Phase8ProductionExecutor

    repository, targets, revision = _repo(tmp_path)
    config = OperationConfig(
        data_dir=tmp_path / "operation-state",
        provider_id="fake",
        model="deterministic",
        worker_id="phase8-production-executor",
        idle_sleep_seconds=0.01,
    )
    root = OperationService.submit(config, "run one bounded production composition")
    proposal = RootPlanningProposal(
        parent_task_id=root.task_id,
        proposal_id="phase8-real-executor-proposal",
        rationale="two independent workers and one integrated continuation",
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
                objective="continue after both changes are integrated",
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

    with OperationService.open(config) as operation:
        parent = operation.store.load_task(root.task_id)
        assert parent is not None
        operation.apply_planning_proposal(proposal, execution_owner="devfarm")
        candidate = DevelopmentPlanningBridge(repository).build_candidate(
            parent,
            proposal,
            run_id="phase8-real-executor-run",
            base_revision=revision,
            task_specs={
                "worker-a": worker_spec(targets[0]),
                "worker-b": worker_spec(targets[1]),
                "continuation": {
                    "owner": "codex",
                    "codex_direct_reason": "continuation is released by Operation after integration",
                },
            },
        )
        for _manifest_path, manifest in candidate.manifests:
            write_manifest(repository, manifest)
        create_plan(repository, candidate.plan)
        commander = CodexSupervisedCommanderRun(repository, "phase8-real-executor-run")
        commander.create(roadmap_reference={"work_address": "5-B-1"})
        commander_plan = commander.plan()
        worker_task_ids = {
            task["planner_child_key"]: task["task_id"]
            for task in commander_plan["tasks"]
            if task["owner"] == "worker"
        }
        operation.handoff_planning_children_to_devfarm(
            proposal_id=proposal.proposal_id,
            run_id="phase8-real-executor-run",
            bindings=worker_task_ids,
        )
        providers = {
            worker_task_ids["worker-a"]: _WorkerProvider(
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
            worker_task_ids["worker-b"]: _WorkerProvider(
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
        executor = Phase8ProductionExecutor(
            operation=operation,
            commander=commander,
            proposal_id=proposal.proposal_id,
            devfarm_run_id="phase8-real-executor-run",
            bindings=worker_task_ids,
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
            final_review_decision=lambda packet, proposal: proposal,
            target_checkout=repository,
        )

        result = executor.advance()

        plan = commander.plan()
        diagnostics = [
            {
                key: task.get(key)
                for key in ("task_id", "status", "block_reason", "last_error", "last_result_status")
                if task.get(key) is not None
            }
            for task in plan["tasks"]
        ]
        assert result["integrated"] == ["worker-a", "worker-b"], {"result": result, "tasks": diagnostics}
        assert result["continuation_ready"] is True
        continuation = next(
            task
            for payload in operation.store.snapshot()["tasks"].values()
            if (task := Task.from_persisted_dict(payload)).metadata.get("planner_child_key") == "continuation"
        )
        assert continuation.status is TaskStatus.QUEUED
