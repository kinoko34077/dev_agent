import hashlib
import json
import subprocess
from datetime import datetime, timezone

import pytest

from scripts.devfarm import DevFarmError, write_manifest
from scripts.devfarm import main as devfarm_main
from scripts.devfarm_commander import (
    CommanderPlanStore,
    PlanConflictError,
    collect_plan,
    create_plan,
    dispatch_cli,
    dispatch_plan,
    list_active_ownership,
    mark_integrated,
    recover_orphaned_dispatches,
    reassign_task,
    refresh_plan,
    resume_plan,
    summarize_delegation,
    supersede_plan,
    validate_plan,
    verify_plan,
)
from scripts.devfarm_orchestrator import DevFarmOrchestrator, HostConcurrencyGovernor, RemoteConcurrencyGovernor
from scripts.devfarm_supervisor import CodexSupervisedCommanderRun, main as supervisor_main
from src.dev_agent.coordination import WorkAddress
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.fake.provider import FakeProvider


class _WorkerProvider(FakeProvider):
    provider_id = "cloudflare"
    provider_binding_id = "cloudflare"
    model_id = "@cf/meta/llama-3.1-8b-instruct"
    intelligence_tier = "L1"

    def __init__(self, output):
        self.output = output

    def request(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            provider=self.provider_id,
            model=self.model_id,
            text_segments=[json.dumps(self.output)],
        )


class _FailingVerifier:
    def verify(self, root, manifest_paths):
        raise RuntimeError("verification boundary unavailable")


class _ResultFailingVerifier:
    def verify(self, root, manifest_paths):
        return [
            {
                "status": "failed",
                "changed_files": [],
                "tests_run": [],
                "tests_passed": False,
                "known_issues": ["worker patch apply check failed: malformed hunk"],
                "assumptions": [],
                "worker_metrics": {},
            }
            for _ in manifest_paths
        ]


class _OneProposalExplodes(DevFarmOrchestrator):
    def _propose(self, root, assignment):
        if assignment.manifest_path.stem == "worker-a":
            raise RuntimeError("unexpected worker exception")
        return super()._propose(root, assignment)


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", f"safe.directory={cwd.as_posix()}", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _patch(path):
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,2 +1,3 @@\n"
        " def test_target():\n"
        "     assert True\n"
        "+    return None\n"
    )


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "commander-tests@example.invalid")
    _git(root, "config", "user.name", "Commander Tests")
    targets = ["tests/v2/worker_a.py", "tests/v2/worker_b.py"]
    for target in targets:
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_target():\n    assert True\n", encoding="utf-8")
    baseline = root / "tests/v2/baseline.py"
    baseline.write_text("def test_baseline():\n    assert True\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "commander baseline")
    return root, targets, _git(root, "rev-parse", "HEAD").stdout.strip()


def _manifest(root, revision, task_id, target):
    return write_manifest(
        root,
        {
            "task_id": task_id,
            "objective": "Add a harmless return to the focused test.",
            "base_revision": revision,
            "allowed_files": [target],
            "read_files": [target],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["cloudflare"],
            "outbound_files": [target],
            "requirements": [],
            "acceptance": ["focused test passes"],
            "test_commands": [f"python -m pytest {target} tests/v2/baseline.py -q"],
            "max_attempts": 2,
            "output_contract": {},
        },
    )


def _integrate_worker_patch(root, task_id, task):
    attempt_id = task["last_attempt_id"]
    patch_path = root / ".devfarm" / "results" / task_id / "attempts" / attempt_id / "patch.diff"
    patch = patch_path.read_text(encoding="utf-8")
    subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "apply", "--whitespace=error", "-"],
        cwd=root,
        input=patch,
        capture_output=True,
        text=True,
        check=True,
    )
    _git(root, "add", "--all")
    _git(root, "commit", "-m", f"integrate {task_id}")
    revision = _git(root, "rev-parse", "HEAD").stdout.strip()
    digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    return revision, attempt_id, digest


def _integrate_codex_review(root):
    review = root / "docs" / "commander-review.md"
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text("# Commander review\n", encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "commit", "-m", "integrate Codex review")
    revision = _git(root, "rev-parse", "HEAD").stdout.strip()
    diff = _git(root, "diff-tree", "--root", "--binary", "--no-commit-id", "-r", revision, "--").stdout
    return revision, hashlib.sha256(diff.encode("utf-8")).hexdigest()


def _plan(root, revision, targets):
    manifests = [_manifest(root, revision, f"worker-{letter}", target) for letter, target in zip(("a", "b"), targets)]
    return create_plan(
        root,
        {
            "run_id": "commander-run-001",
            "objective": "Run two independent bounded worker tasks, then review them.",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                },
                {
                    "task_id": "worker-b",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-b.json",
                    "ownership": [targets[1]],
                    "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                },
                {
                    "task_id": "codex-review",
                    "owner": "codex",
                    "dependencies": ["worker-a", "worker-b"],
                    "ownership": ["docs/commander-review.md"],
                },
            ],
        },
    )


def test_commander_plan_dispatch_verify_resume_and_integrate(tmp_path):
    root, targets, revision = _repo(tmp_path)
    plan = _plan(root, revision, targets)
    assert plan["status"] == "READY"
    assert {task["status"] for task in plan["tasks"][:2]} == {"READY"}
    assert plan["tasks"][2]["status"] == "PLANNED"

    providers = {
        "worker-a": _WorkerProvider({"status": "completed", "changed_files": [targets[0]], "tests_run": [], "tests_passed": True, "known_issues": [], "assumptions": [], "patch": _patch(targets[0]), "notes": "ready"}),
        "worker-b": _WorkerProvider({"status": "completed", "changed_files": [targets[1]], "tests_run": [], "tests_passed": True, "known_issues": [], "assumptions": [], "patch": _patch(targets[1]), "notes": "ready"}),
    }
    orchestrator = DevFarmOrchestrator(
        remote_governor=RemoteConcurrencyGovernor(max_inflight=2),
        host_governor=HostConcurrencyGovernor(worktree_verification_slots=1),
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )
    proposed = dispatch_plan(root, "commander-run-001", providers=providers, orchestrator=orchestrator)
    assert {task["status"] for task in proposed["tasks"][:2]} == {"PROPOSED"}
    for task in proposed["tasks"][:2]:
        assert task["dispatch_id"]
        assert datetime.fromisoformat(task["dispatch_started_at"]).tzinfo is not None
        assert datetime.fromisoformat(task["dispatch_deadline_at"]).tzinfo is not None
        assert task["dispatch_owner_pid"] > 0
    assert not (root / ".devfarm/worktrees/worker-a").exists()
    assert not (root / ".devfarm/worktrees/worker-b").exists()

    verified = verify_plan(root, "commander-run-001", orchestrator=orchestrator)
    assert {task["status"] for task in verified["tasks"][:2]} == {"HOST_VERIFIED"}
    resumed = resume_plan(root, "commander-run-001")
    assert resumed["tasks"][2]["status"] == "PLANNED"
    assert len(resumed["results"]) >= 4

    a_revision, a_attempt, a_digest = _integrate_worker_patch(root, "worker-a", verified["tasks"][0])
    mark_integrated(
        root,
        "commander-run-001",
        "worker-a",
        note="Codex reviewed the verified patch",
        target_ref="HEAD",
        integration_revision=a_revision,
        source_attempt_id=a_attempt,
        verified_patch_digest=a_digest,
    )
    still_waiting = CommanderPlanStore(root).load("commander-run-001")
    assert still_waiting["tasks"][2]["status"] == "PLANNED"
    b_revision, b_attempt, b_digest = _integrate_worker_patch(root, "worker-b", still_waiting["tasks"][1])
    mark_integrated(
        root,
        "commander-run-001",
        "worker-b",
        note="Codex reviewed the verified patch",
        target_ref="HEAD",
        integration_revision=b_revision,
        source_attempt_id=b_attempt,
        verified_patch_digest=b_digest,
    )
    released = CommanderPlanStore(root).load("commander-run-001")
    assert released["tasks"][2]["status"] == "READY"
    c_revision, c_digest = _integrate_codex_review(root)
    mark_integrated(
        root,
        "commander-run-001",
        "codex-review",
        note="Codex completed the integration review",
        target_ref="HEAD",
        integration_revision=c_revision,
        source_attempt_id="codex-review",
        verified_patch_digest=c_digest,
    )
    final = CommanderPlanStore(root).load("commander-run-001")
    assert final["tasks"][0]["status"] == "INTEGRATED"
    assert final["tasks"][2]["status"] == "INTEGRATED"


def test_commander_preserves_work_address_and_rejects_duplicate_addresses(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    valid = create_plan(
        root,
        {
            "run_id": "work-address-valid",
            "objective": "preserve task identity and display position",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "work_address": "5-B",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                },
                {
                    "task_id": "codex-review",
                    "owner": "codex",
                    "work_address": "5-B-1",
                    "ownership": ["docs/review.md"],
                },
            ],
        },
    )
    assert valid["tasks"][0]["work_address"] == "5-B"
    assert valid["tasks"][1]["work_address"] == "5-B-1"

    with pytest.raises(DevFarmError, match="work_address"):
        create_plan(
            root,
            {
                "run_id": "work-address-duplicate",
                "objective": "reject duplicate display positions",
                "base_revision": revision,
                "tasks": [
                    {
                        "task_id": "worker-a-duplicate",
                        "owner": "codex",
                        "work_address": "5-B",
                        "ownership": ["docs/duplicate-a.md"],
                    },
                    {
                        "task_id": "codex-duplicate",
                        "owner": "codex",
                        "work_address": "5-B",
                        "ownership": ["docs/duplicate-b.md"],
                    },
                ],
            },
        )


def test_commander_auto_assigns_work_addresses_and_preserves_parallel_parentage():
    normalized = validate_plan(
        {
            "run_id": "work-address-auto",
            "objective": "allocate positions without replacing UUID identity",
            "base_revision": "abc123",
            "tasks": [
                {"task_id": "parent", "owner": "codex", "work_address": "5-B"},
                {
                    "task_id": "lane-a",
                    "owner": "codex",
                    "work_address_parent": "5-B",
                    "work_address_kind": "letter",
                },
                {
                    "task_id": "lane-b",
                    "owner": "codex",
                    "work_address_parent": "5-B",
                    "work_address_kind": "letter",
                },
                {"task_id": "next-root", "owner": "codex"},
            ],
        }
    )

    by_id = {task["task_id"]: task for task in normalized["tasks"]}
    assert by_id["parent"]["work_address"] == "5-B"
    assert by_id["lane-a"]["work_address"] == "5-B-A"
    assert by_id["lane-b"]["work_address"] == "5-B-B"
    assert by_id["next-root"]["work_address"] == "1"
    assert by_id["lane-a"]["node_type"] == "task"


def test_commander_exposes_active_file_ownership_without_claiming_or_releasing(tmp_path):
    root, targets, revision = _repo(tmp_path)
    create_plan(
        root,
        {
            "run_id": "ownership-query",
            "objective": "inspect active file ownership",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "owner-a",
                    "owner": "codex",
                    "ownership": [targets[0]],
                },
                {
                    "task_id": "owner-b",
                    "owner": "codex",
                    "ownership": [targets[1]],
                },
            ],
        },
    )

    records = list_active_ownership(root)
    assert [(item["task_id"], item["path"]) for item in records] == [
        ("owner-a", targets[0]),
        ("owner-b", targets[1]),
    ]
    assert all(item["status"] == "READY" for item in records)
    assert {item["work_address"] for item in records} == {"1", "2"}
    assert list_active_ownership(root, run_id="ownership-query") == records


def test_new_plan_can_compare_against_legacy_plan_with_now_protected_manifest(tmp_path):
    root, _targets, revision = _repo(tmp_path)
    legacy_directory = root / ".devfarm" / "plans"
    legacy_directory.mkdir(parents=True, exist_ok=True)
    (legacy_directory / "legacy-protected-plan.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "legacy-protected-plan",
                "objective": "historical worker plan",
                "base_revision": revision,
                "status": "REJECTED",
                "tasks": [
                    {
                        "task_id": "legacy-protected-task",
                        "owner": "worker",
                        "status": "REJECTED",
                        "task_type": "worker",
                        "risk": "normal",
                        "sensitivity": "normal",
                        "dependencies": [],
                        "dependency_types": {},
                        "ownership": ["src/dev_agent/intelligence/refinement.py"],
                        "manifest_path": ".devfarm/tasks/legacy-protected-task.json",
                        "max_attempts": 1,
                        "attempt_count": 1,
                        "worker_candidate": True,
                        "delegation_reason": "worker_assignment",
                        "assignment": {
                            "owner": "worker",
                            "provider_id": "cloudflare",
                            "model_id": "worker-model",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    plan = create_plan(
        root,
        {
            "run_id": "new-plan-after-legacy",
            "objective": "create an unrelated plan without releasing legacy ownership",
            "base_revision": revision,
            "tasks": [{"task_id": "new-task", "owner": "codex", "ownership": ["docs/new.md"]}],
        },
    )

    assert plan["status"] == "READY"
    ownership = list_active_ownership(root)
    assert {(item["run_id"], item["path"]) for item in ownership} == {
        ("legacy-protected-plan", "src/dev_agent/intelligence/refinement.py"),
        ("new-plan-after-legacy", "docs/new.md"),
    }


def test_supervisor_approved_integration_applies_patch_and_records_git_proof(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "supervisor-integration-run",
            "objective": "integrate one explicitly approved worker patch",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
        },
    )
    provider = _WorkerProvider(
        {
            "status": "completed",
            "changed_files": [targets[0]],
            "tests_run": [],
            "tests_passed": True,
            "known_issues": [],
            "assumptions": [],
            "patch": _patch(targets[0]),
            "notes": "ready",
        }
    )
    orchestrator = DevFarmOrchestrator(
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )
    runner = CodexSupervisedCommanderRun(root, "supervisor-integration-run")
    runner.create()
    review_step = runner.run_until_intervention(
        providers={"worker-a": provider},
        orchestrator=orchestrator,
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
        max_wait_seconds=60,
        sleep_fn=lambda _seconds: (_ for _ in ()).throw(AssertionError("completed worker must not sleep")),
    )
    assert review_step.status == "REVIEWING"
    assert review_step.metrics["codex_review_request_count"] == 1
    assert review_step.metrics["codex_review_count"] == 0
    assert len(review_step.review_packets) == 1
    assert "patch" not in review_step.review_packets[0]
    assert review_step.review_packets[0]["verification_summary"]["host_verified"] is True
    assert review_step.review_packets[0]["verification_summary"]["host_tests_passed"] is True
    assert review_step.review_packets[0]["verification_summary"]["independent_verification"] is True
    assert review_step.review_packets[0]["patch_sha256"] == hashlib.sha256(
        _patch(targets[0]).encode("utf-8")
    ).hexdigest()
    stale_plan = runner.plan()
    stale_plan["supervisor"]["review_packets"][0]["verification_summary"]["host_verified"] = False
    runner.store.save(stale_plan, expected_revision=stale_plan["plan_revision"])
    refreshed = runner.advance(providers={}, orchestrator=orchestrator)
    assert refreshed.review_packets[0]["verification_summary"]["host_verified"] is True
    task = runner.plan()["tasks"][0]
    with pytest.raises(DevFarmError, match="approval"):
        runner.integrate_approved_worker(
            "worker-a",
            decision_id="missing-review",
            commit_message="integrate worker patch",
            target_checkout=root,
            target_ref="HEAD",
        )

    decision_step = runner.record_review_decision(
        "worker-a",
        attempt_id=task["last_attempt_id"],
        decision="APPROVE_INTEGRATION",
        evidence_refs=[{"kind": "verification", "path": task["result_ref"]}],
    )
    decision_id = runner.plan()["review_decisions"][0]["decision_id"]
    integrated = runner.integrate_approved_worker(
        "worker-a",
        decision_id=decision_id,
        commit_message="integrate worker patch",
        target_checkout=root,
        target_ref="HEAD",
    )

    assert decision_step.status == "INTEGRATING"
    assert integrated.plan_status == "INTEGRATED"
    integrated_plan = runner.plan()
    integrated_task = integrated_plan["tasks"][0]
    assert integrated_task["status"] == "INTEGRATED"
    assert integrated_task["integration_revision"] == _git(root, "rev-parse", "HEAD").stdout.strip()
    assert "return None" in (root / targets[0]).read_text(encoding="utf-8")


def test_supervisor_rework_decision_creates_next_attempt_manifest(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "supervisor-rework-run",
            "objective": "rework one verified worker proposal",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
        },
    )
    provider = _WorkerProvider(
        {
            "status": "completed",
            "changed_files": [targets[0]],
            "tests_run": [],
            "tests_passed": True,
            "known_issues": [],
            "assumptions": [],
            "patch": _patch(targets[0]),
        }
    )
    orchestrator = DevFarmOrchestrator(
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )
    dispatch_plan(root, "supervisor-rework-run", providers={"worker-a": provider}, orchestrator=orchestrator)
    verify_plan(root, "supervisor-rework-run", orchestrator=orchestrator)
    runner = CodexSupervisedCommanderRun(root, "supervisor-rework-run")
    runner.create()
    runner.advance(providers={})
    task = runner.plan()["tasks"][0]
    attempt_id = task["last_attempt_id"]
    with pytest.raises(DevFarmError, match="required_correction"):
        runner.record_review_decision(
            "worker-a",
            attempt_id=attempt_id,
            decision="REWORK",
            evidence_refs=[{"kind": "verification", "path": task["result_ref"]}],
        )
    runner.record_review_decision(
        "worker-a",
        attempt_id=attempt_id,
        decision="REWORK",
        required_correction="Address the review finding before another verification.",
        evidence_refs=[{"kind": "verification", "path": task["result_ref"]}],
    )
    assert runner.plan()["tasks"][0]["status"] == "REJECTED"
    assert runner.plan()["results"][-1]["stage"] == "review"
    assert collect_plan(root, "supervisor-rework-run")["tasks"][0]["status"] == "REJECTED"

    handoff = runner.rework_handoff(
        "worker-a",
        failure_evidence_reference={"type": "result", "path": task["result_ref"]},
        review_findings_reference={"type": "review", "attempt_id": attempt_id},
        required_correction="Address the review finding before another verification.",
    )
    reassigned = runner.reassign(
        "worker-a",
        provider_id="cloudflare",
        model_id="worker-model",
        rework_handoff=handoff,
    )
    new_task = reassigned and runner.plan()["tasks"][0]
    assert new_task["status"] == "READY"
    assert new_task.get("last_attempt_id") is None
    # The previous latest projection remains on disk, but cannot be collected
    # as the new attempt before the new manifest is dispatched.
    assert runner.plan()["tasks"][0]["manifest_path"] != ".devfarm/tasks/worker-a.json"
    assert collect_plan(root, "supervisor-rework-run")["tasks"][0]["status"] == "READY"
    new_manifest = json.loads((root / new_task["manifest_path"]).read_text(encoding="utf-8"))
    assert new_manifest["rework_handoff"]["kind"] == "repair_request"
    dispatch_plan(root, "supervisor-rework-run", providers={"worker-a": provider}, orchestrator=orchestrator)
    assert runner.plan()["tasks"][0]["status"] == "PROPOSED"


def test_supervisor_cli_review_and_integrate_commands_use_existing_authority(tmp_path, capsys):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "supervisor-cli-integration-run",
            "objective": "exercise the daily review and integration CLI",
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 1,
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
            "base_revision": revision,
        },
    )
    provider = _WorkerProvider(
        {
            "status": "completed",
            "changed_files": [targets[0]],
            "tests_run": [],
            "tests_passed": True,
            "known_issues": [],
            "assumptions": [],
            "patch": _patch(targets[0]),
            "notes": "ready",
        }
    )
    orchestrator = DevFarmOrchestrator(
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )
    runner = CodexSupervisedCommanderRun(root, "supervisor-cli-integration-run")
    runner.create()
    runner.run_until_intervention(
        providers={"worker-a": provider},
        orchestrator=orchestrator,
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
        max_wait_seconds=30,
        sleep_fn=lambda _seconds: (_ for _ in ()).throw(AssertionError("verified worker must not sleep")),
    )
    task = runner.plan()["tasks"][0]
    assert supervisor_main(
        [
            "review",
            "supervisor-cli-integration-run",
            "worker-a",
            "--root",
            str(root),
            "--attempt-id",
            task["last_attempt_id"],
            "--decision",
            "APPROVE_INTEGRATION",
            "--evidence-ref",
            task["result_ref"],
        ]
    ) == 0
    decision_id = runner.plan()["review_decisions"][0]["decision_id"]
    assert supervisor_main(
        [
            "integrate",
            "supervisor-cli-integration-run",
            "worker-a",
            "--root",
            str(root),
            "--decision-id",
            decision_id,
            "--target-checkout",
            str(root),
            "--target-ref",
            "HEAD",
            "--commit-message",
            "integrate CLI worker patch",
        ]
    ) == 0
    assert runner.plan()["tasks"][0]["status"] == "INTEGRATED"
    capsys.readouterr()


def test_supervisor_cli_rework_requires_review_and_creates_new_manifest(tmp_path, capsys):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "supervisor-cli-rework-run",
            "objective": "exercise the daily rework CLI",
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
            "base_revision": revision,
        },
    )
    provider = _WorkerProvider(
        {
            "status": "completed",
            "changed_files": [targets[0]],
            "tests_run": [],
            "tests_passed": True,
            "known_issues": [],
            "assumptions": [],
            "patch": _patch(targets[0]),
        }
    )
    orchestrator = DevFarmOrchestrator(
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )
    runner = CodexSupervisedCommanderRun(root, "supervisor-cli-rework-run")
    runner.create()
    runner.run_until_intervention(
        providers={"worker-a": provider},
        orchestrator=orchestrator,
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
        max_wait_seconds=30,
        sleep_fn=lambda _seconds: (_ for _ in ()).throw(AssertionError("verified worker must not sleep")),
    )
    task = runner.plan()["tasks"][0]
    correction = "Address the focused review finding before another attempt."
    assert supervisor_main(
        [
            "review",
            "supervisor-cli-rework-run",
            "worker-a",
            "--root",
            str(root),
            "--attempt-id",
            task["last_attempt_id"],
            "--decision",
            "REWORK",
            "--required-correction",
            correction,
            "--evidence-ref",
            task["result_ref"],
        ]
    ) == 0
    old_manifest = task["manifest_path"]
    assert supervisor_main(
        [
            "rework",
            "supervisor-cli-rework-run",
            "worker-a",
            "--root",
            str(root),
            "--failure-evidence-ref",
            task["result_ref"],
        ]
    ) == 0
    updated = runner.plan()["tasks"][0]
    assert updated["status"] == "READY"
    assert updated["manifest_path"] != old_manifest
    capsys.readouterr()


def test_supervisor_cli_rework_after_host_failure_uses_bounded_correction(tmp_path, capsys):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "supervisor-cli-host-failure-rework-run",
            "objective": "recover a worker patch rejected by Host Verification",
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
            "base_revision": revision,
        },
    )
    provider = _WorkerProvider(
        {
            "status": "completed",
            "changed_files": [targets[0]],
            "tests_run": [],
            "tests_passed": True,
            "known_issues": [],
            "assumptions": [],
            "patch": _patch(targets[0]),
        }
    )
    dispatch_plan(root, "supervisor-cli-host-failure-rework-run", providers={"worker-a": provider})
    failed = verify_plan(
        root,
        "supervisor-cli-host-failure-rework-run",
        orchestrator=_FailingVerifier(),
    )
    task = failed["tasks"][0]
    assert task["status"] == "REJECTED"
    assert task["block_reason"] == "host_verification_failed"
    old_manifest = task["manifest_path"]
    correction = "Regenerate a normal modification diff for the existing file; do not emit a new-file patch."

    assert supervisor_main(
        [
            "rework",
            "supervisor-cli-host-failure-rework-run",
            "worker-a",
            "--root",
            str(root),
            "--failure-evidence-ref",
            task["result_ref"],
            "--required-correction",
            correction,
        ]
    ) == 0

    updated = CodexSupervisedCommanderRun(root, "supervisor-cli-host-failure-rework-run").plan()
    assert updated["tasks"][0]["status"] == "READY"
    assert updated["tasks"][0]["manifest_path"] != old_manifest
    assert updated["review_decisions"] == []
    new_manifest = json.loads((root / updated["tasks"][0]["manifest_path"]).read_text(encoding="utf-8"))
    assert new_manifest["rework_handoff"]["kind"] == "repair_request"
    assert new_manifest["rework_handoff"]["requirements"] == [correction]
    capsys.readouterr()


def test_supervisor_cli_rejects_wrong_attempt_and_invalid_decision(tmp_path, capsys):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "supervisor-cli-validation-run",
            "objective": "exercise supervisor CLI validation",
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 1,
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
            "base_revision": revision,
        },
    )
    runner = CodexSupervisedCommanderRun(root, "supervisor-cli-validation-run")
    runner.create()
    with pytest.raises(SystemExit):
        supervisor_main(
            [
                "review",
                "supervisor-cli-validation-run",
                "worker-a",
                "--root",
                str(root),
                "--attempt-id",
                "attempt-not-valid",
                "--decision",
                "NOT_A_DECISION",
            ]
        )
    with pytest.raises(DevFarmError, match="attempt"):
        supervisor_main(
            [
                "review",
                "supervisor-cli-validation-run",
                "worker-a",
                "--root",
                str(root),
                "--attempt-id",
                "attempt-not-valid",
                "--decision",
                "APPROVE_INTEGRATION",
            ]
        )
    assert runner.plan().get("review_decisions", []) == []
    capsys.readouterr()


def test_supervisor_reject_decision_is_terminal_across_resume(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "supervisor-reject-run",
            "objective": "persist an explicit review rejection",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "status": "HOST_VERIFIED",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "last_attempt_id": "attempt-001",
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
        },
    )
    runner = CodexSupervisedCommanderRun(root, "supervisor-reject-run")
    runner.create()

    decision_step = runner.record_review_decision(
        "worker-a",
        attempt_id="attempt-001",
        decision="REJECT",
        findings=["The candidate does not satisfy the acceptance contract."],
    )

    assert decision_step.status == "BLOCKED"
    assert runner.plan()["status"] == "REJECTED"
    assert runner.plan()["tasks"][0]["status"] == "REJECTED"

    resumed = runner.advance(providers={})
    assert resumed.status == "BLOCKED"
    assert resumed.metrics["codex_review_request_count"] == 0


def test_commander_keeps_sibling_proposal_when_one_worker_raises(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _plan(root, revision, targets)
    providers = {
        "worker-a": _WorkerProvider(
            {
                "status": "completed",
                "changed_files": [targets[0]],
                "tests_run": [],
                "tests_passed": True,
                "known_issues": [],
                "assumptions": [],
                "patch": _patch(targets[0]),
            }
        ),
        "worker-b": _WorkerProvider(
            {
                "status": "completed",
                "changed_files": [targets[1]],
                "tests_run": [],
                "tests_passed": True,
                "known_issues": [],
                "assumptions": [],
                "patch": _patch(targets[1]),
            }
        ),
    }

    dispatched = dispatch_plan(
        root,
        "commander-run-001",
        providers=providers,
        orchestrator=_OneProposalExplodes(),
    )

    by_id = {task["task_id"]: task for task in dispatched["tasks"]}
    assert by_id["worker-a"]["status"] == "REJECTED"
    assert by_id["worker-a"]["block_reason"] == "proposal_failed"
    assert by_id["worker-b"]["status"] == "PROPOSED"
    assert any(
        item["task_id"] == "worker-b" and item["status"] == "completed"
        for item in dispatched["results"]
    )


def test_commander_rejects_overlapping_ownership(tmp_path):
    root, _targets, revision = _repo(tmp_path)
    with pytest.raises(DevFarmError, match="ownership"):
        create_plan(
            root,
            {
                "run_id": "overlap-run",
                "objective": "reject overlap",
                "base_revision": revision,
                "tasks": [
                    {"task_id": "a", "owner": "codex", "ownership": ["src/shared.py"]},
                    {"task_id": "b", "owner": "codex", "ownership": ["src/shared.py"]},
                ],
            },
        )


def test_commander_rejects_cross_plan_active_ownership_overlap(tmp_path):
    root, _targets, revision = _repo(tmp_path)
    create_plan(
        root,
        {
            "run_id": "active-owner",
            "objective": "hold a source path",
            "base_revision": revision,
            "tasks": [
                {"task_id": "owner", "owner": "codex", "ownership": ["src/shared.py"]},
            ],
        },
    )
    with pytest.raises(DevFarmError, match="active ownership conflict"):
        create_plan(
            root,
            {
                "run_id": "overlapping-plan",
                "objective": "do not double claim a source path",
                "base_revision": revision,
                "tasks": [
                    {"task_id": "contender", "owner": "codex", "ownership": ["src/shared.py"]},
                ],
            },
        )


def test_commander_reassigns_a_failed_worker_within_attempt_limit(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    plan = create_plan(
        root,
        {
            "run_id": "reassign-run",
            "objective": "bounded reassignment",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "status": "BLOCKED",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                        "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                }
            ],
        },
    )
    assert plan["status"] == "BLOCKED"
    reassigned = reassign_task(
        root,
        "reassign-run",
        "worker-a",
        provider_id="openrouter",
        model_id="openrouter/free",
    )
    assert reassigned["tasks"][0]["status"] == "READY"
    assert reassigned["tasks"][0]["assignment"]["provider_id"] == "openrouter"


def test_commander_can_explicitly_supersede_a_terminal_plan_and_release_ownership(tmp_path):
    root, _targets, revision = _repo(tmp_path)
    create_plan(
        root,
        {
            "run_id": "supersede-run",
            "objective": "release a terminal invalid plan",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "invalid-worker",
                    "owner": "codex",
                    "status": "REJECTED",
                    "ownership": ["src/shared.py"],
                    "max_attempts": 1,
                }
            ],
        },
    )

    superseded = supersede_plan(
        root,
        "supersede-run",
        reason="worker input manifest referenced a missing test file",
    )

    assert superseded["status"] == "SUPERSEDED"
    assert superseded["tasks"][0]["status"] == "SUPERSEDED"
    assert superseded["tasks"][0]["last_error"] == "worker input manifest referenced a missing test file"
    assert list_active_ownership(root) == []


def test_commander_reassign_with_rework_creates_immutable_manifest_revision(tmp_path):
    root, targets, revision = _repo(tmp_path)
    original = _manifest(root, revision, "worker-a", targets[0]).relative_to(root).as_posix()
    plan = create_plan(
        root,
        {
            "run_id": "rework-manifest-run",
            "objective": "reassign with review correction",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "status": "REJECTED",
                    "manifest_path": original,
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
        },
    )

    reworked = reassign_task(
        root,
        "rework-manifest-run",
        "worker-a",
        provider_id="openrouter",
        model_id="openrouter/free",
        rework_handoff={
            "kind": "repair_request",
            "requirements": ["Fix the failed assertion."],
            "payload_reference": {"type": "result", "path": ".devfarm/results/worker-a/result.json"},
        },
    )

    task = reworked["tasks"][0]
    assert task["status"] == "READY"
    assert task["manifest_path"] != original
    assert original in task["manifest_history"]
    assert (root / original).is_file()
    new_manifest = json.loads((root / task["manifest_path"]).read_text(encoding="utf-8"))
    assert new_manifest["base_revision"] == revision
    assert new_manifest["rework_handoff"]["kind"] == "repair_request"
    assert reworked["plan_revision"] == plan["plan_revision"] + 1


def test_commander_persists_host_verification_boundary_failure(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "verification-failure-run",
            "objective": "persist verifier failure",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                        "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                }
            ],
        },
    )
    dispatch_plan(
        root,
        "verification-failure-run",
        providers={"worker-a": _WorkerProvider({"status": "completed", "changed_files": [targets[0]], "tests_run": [], "tests_passed": True, "known_issues": [], "assumptions": [], "patch": _patch(targets[0]), "notes": "ready"})},
    )

    result = verify_plan(root, "verification-failure-run", orchestrator=_FailingVerifier())

    assert result["tasks"][0]["status"] == "REJECTED"
    assert result["tasks"][0]["block_reason"] == "host_verification_failed"
    assert "verification boundary unavailable" in result["tasks"][0]["last_error"]
    assert any(item["stage"] == "host_verification" and item["status"] == "failed" for item in result["results"])

    recovered = verify_plan(root, "verification-failure-run", verification_trust_level="TRUSTED_HOST_EXEC", operator_approved=True)
    assert recovered["tasks"][0]["status"] == "HOST_VERIFIED"
    assert "block_reason" not in recovered["tasks"][0]
    assert "last_error" not in recovered["tasks"][0]


def test_commander_persists_verifier_result_failure_reason(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "verification-result-failure-run",
            "objective": "persist verifier result failure reason",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                }
            ],
        },
    )
    dispatch_plan(
        root,
        "verification-result-failure-run",
        providers={"worker-a": _WorkerProvider({"status": "completed", "changed_files": [targets[0]], "tests_run": [], "tests_passed": True, "known_issues": [], "assumptions": [], "patch": _patch(targets[0]), "notes": "ready"})},
    )

    result = verify_plan(root, "verification-result-failure-run", orchestrator=_ResultFailingVerifier())

    task = result["tasks"][0]
    assert task["status"] == "REJECTED"
    assert "malformed hunk" in task["last_error"]


def test_commander_clears_stale_verification_error_after_reassigned_success(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "verification-retry-clean-run",
            "objective": "clear stale verification state after a successful retry",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                    "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                }
            ],
        },
    )
    provider = _WorkerProvider(
        {
            "status": "completed",
            "changed_files": [targets[0]],
            "tests_run": [],
            "tests_passed": True,
            "known_issues": [],
            "assumptions": [],
            "patch": _patch(targets[0]),
            "notes": "ready",
        }
    )
    dispatch_plan(root, "verification-retry-clean-run", providers={"worker-a": provider})
    failed = verify_plan(root, "verification-retry-clean-run", orchestrator=_FailingVerifier())
    assert failed["tasks"][0]["status"] == "REJECTED"

    reassign_task(
        root,
        "verification-retry-clean-run",
        "worker-a",
        provider_id="cloudflare",
        model_id="@cf/meta/llama-3.1-8b-instruct",
    )
    dispatch_plan(root, "verification-retry-clean-run", providers={"worker-a": provider})
    verified = verify_plan(root, "verification-retry-clean-run", verification_trust_level="TRUSTED_HOST_EXEC", operator_approved=True)

    task = verified["tasks"][0]
    assert task["status"] == "HOST_VERIFIED"
    assert "block_reason" not in task
    assert "last_error" not in task


def test_commander_rejects_dependency_cycles_and_cli_can_read_status(tmp_path, capsys):
    root, _targets, revision = _repo(tmp_path)
    with pytest.raises(DevFarmError, match="cycle"):
        create_plan(
            root,
            {
                "run_id": "cycle-run",
                "objective": "reject cycle",
                "base_revision": revision,
                "tasks": [
                    {"task_id": "a", "owner": "codex", "dependencies": ["b"], "ownership": ["docs/a.md"]},
                    {"task_id": "b", "owner": "codex", "dependencies": ["a"], "ownership": ["docs/b.md"]},
                ],
            },
        )
    create_plan(
        root,
        {
            "run_id": "cli-run",
            "objective": "show status",
            "base_revision": revision,
            "tasks": [{"task_id": "codex-task", "owner": "codex", "ownership": ["docs/cli.md"]}],
        },
    )
    assert devfarm_main(["status", "cli-run", "--root", str(root)]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["run_id"] == "cli-run"
    assert output["tasks"][0]["status"] == "READY"


def test_commander_plan_save_uses_revision_cas(tmp_path):
    root, _targets, revision = _repo(tmp_path)
    store = CommanderPlanStore(root)
    plan = create_plan(
        root,
        {
            "run_id": "cas-run",
            "objective": "prevent lost plan updates",
            "base_revision": revision,
            "tasks": [{"task_id": "task", "owner": "codex", "ownership": ["docs/task.md"]}],
        },
    )
    first = store.load("cas-run")
    second = store.load("cas-run")
    first["tasks"][0]["integration_note"] = "first update"
    saved = store.save(first)
    assert saved["plan_revision"] == plan["plan_revision"] + 1
    second["tasks"][0]["integration_note"] = "stale update"
    with pytest.raises(PlanConflictError, match="revision conflict"):
        store.save(second)
    assert store.load("cas-run")["tasks"][0]["integration_note"] == "first update"


def test_commander_recovers_expired_dispatched_task_without_blind_retry(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    plan = create_plan(
        root,
        {
            "run_id": "orphaned-dispatch-run",
            "objective": "recover a lost worker process",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "max_attempts": 2,
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
        },
    )
    task = plan["tasks"][0]
    task.update(
        {
            "status": "DISPATCHED",
            "attempt_count": 1,
            "dispatch_id": "dispatch-001",
            "dispatch_started_at": "2026-01-01T00:00:00+00:00",
            "dispatch_deadline_at": "2026-01-01T00:01:00+00:00",
            "dispatch_owner_pid": 999999,
        }
    )
    CommanderPlanStore(root).save(plan, expected_revision=plan["plan_revision"])

    recovered = recover_orphaned_dispatches(
        root,
        "orphaned-dispatch-run",
        now=datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc),
    )

    recovered_task = recovered["tasks"][0]
    assert recovered_task["status"] == "BLOCKED"
    assert recovered_task["block_reason"] == "orphaned_dispatch"
    assert recovered_task["dispatch_recovery"] == "reconciliation_required"
    assert recovered_task["attempt_count"] == 1
    assert recovered["results"][-1]["stage"] == "dispatch_recovery"
    with pytest.raises(DevFarmError, match="reconciliation"):
        reassign_task(
            root,
            "orphaned-dispatch-run",
            "worker-a",
            provider_id="cloudflare",
            model_id="worker-model",
        )


def test_delegation_summary_counts_explicit_worker_eligibility_only():
    summary = summarize_delegation(
        {
            "tasks": [
                {"task_id": "worker", "owner": "worker", "status": "INTEGRATED"},
                {"task_id": "codex-review", "owner": "codex", "status": "READY"},
                {
                    "task_id": "codex-direct",
                    "owner": "codex",
                    "status": "INTEGRATED",
                    "worker_candidate": True,
                    "delegation_reason": "cross_cutting",
                },
            ]
        }
    )

    assert summary == {
        "worker_owned_task_count": 1,
        "codex_owned_task_count": 2,
        "worker_integrated_task_count": 1,
        "codex_direct_implementation_count": 1,
        "codex_direct_reasons": ["cross_cutting"],
    }


def test_delegation_summary_requires_a_reason_for_explicit_codex_direct_task():
    with pytest.raises(DevFarmError, match="delegation_reason"):
        summarize_delegation(
            {
                "tasks": [
                    {
                        "task_id": "codex-direct",
                        "owner": "codex",
                        "status": "READY",
                        "worker_candidate": True,
                    }
                ]
            }
        )


def test_commander_rejects_codex_worker_candidate_without_explicit_reason(tmp_path):
    root, _targets, revision = _repo(tmp_path)
    with pytest.raises(DevFarmError, match="explicit delegation_reason"):
        create_plan(
            root,
            {
                "run_id": "missing-delegation-reason",
                "objective": "reject unreasoned Codex ownership",
                "base_revision": revision,
                "tasks": [
                    {
                        "task_id": "codex-direct",
                        "owner": "codex",
                        "worker_candidate": True,
                        "ownership": ["docs/review.md"],
                    }
                ],
            },
        )


def test_commander_keeps_dispatched_task_waiting_before_deadline(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    plan = create_plan(
        root,
        {
            "run_id": "live-dispatch-run",
            "objective": "keep a live dispatch waiting",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                    "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
                }
            ],
        },
    )
    task = plan["tasks"][0]
    task.update(
        {
            "status": "DISPATCHED",
            "attempt_count": 1,
            "dispatch_id": "dispatch-002",
            "dispatch_started_at": "2026-01-01T00:00:00+00:00",
            "dispatch_deadline_at": "2026-01-01T00:10:00+00:00",
            "dispatch_owner_pid": 999999,
        }
    )
    CommanderPlanStore(root).save(plan, expected_revision=plan["plan_revision"])

    waiting = recover_orphaned_dispatches(
        root,
        "live-dispatch-run",
        now=datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc),
    )

    assert waiting["tasks"][0]["status"] == "DISPATCHED"
    assert waiting["tasks"][0]["attempt_count"] == 1


def test_commander_integration_requires_git_evidence_and_records_it(tmp_path):
    root, targets, revision = _repo(tmp_path)
    manifest = _manifest(root, revision, "worker-a", targets[0])
    create_plan(
        root,
        {
            "run_id": "integration-evidence-run",
            "objective": "prove integration with Git evidence",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                        "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                }
            ],
        },
    )
    dispatch_plan(
        root,
        "integration-evidence-run",
        providers={
            "worker-a": _WorkerProvider(
                {
                    "status": "completed",
                    "changed_files": [targets[0]],
                    "tests_run": [],
                    "tests_passed": True,
                    "known_issues": [],
                    "assumptions": [],
                    "patch": _patch(targets[0]),
                    "notes": "ready",
                }
            )
        },
    )
    verified = verify_plan(root, "integration-evidence-run", verification_trust_level="TRUSTED_HOST_EXEC", operator_approved=True)
    task = verified["tasks"][0]
    attempt_id = task["last_attempt_id"]
    patch_path = root / ".devfarm" / "results" / "worker-a" / "attempts" / attempt_id / "patch.diff"
    patch = patch_path.read_text(encoding="utf-8")
    applied = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "apply", "--whitespace=error", "-"],
        cwd=root,
        input=patch,
        capture_output=True,
        text=True,
        check=True,
    )
    assert applied.returncode == 0
    _git(root, "add", targets[0])
    _git(root, "commit", "-m", "integrate worker patch")
    integration_revision = _git(root, "rev-parse", "HEAD").stdout.strip()

    integrated = mark_integrated(
        root,
        "integration-evidence-run",
        "worker-a",
        note="Codex reviewed the verified patch",
        target_ref="HEAD",
        integration_revision=integration_revision,
        source_attempt_id=attempt_id,
        verified_patch_digest=hashlib.sha256(patch.encode("utf-8")).hexdigest(),
    )
    integrated_task = integrated["tasks"][0]
    assert integrated_task["status"] == "INTEGRATED"
    assert integrated_task["target_ref"] == "HEAD"
    assert integrated_task["integration_revision"] == integration_revision
    assert integrated_task["source_attempt_id"] == attempt_id
    assert integrated_task["verified_patch_digest"] == hashlib.sha256(patch.encode("utf-8")).hexdigest()


def test_codex_integration_digest_handles_utf8_commit_content(tmp_path):
    root, _targets, revision = _repo(tmp_path)
    document = root / "docs" / "レビュー.md"
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text("# 統合レビュー\n", encoding="utf-8")
    _git(root, "add", "--all")
    _git(root, "commit", "-m", "integrate UTF-8 review")
    integration_revision = _git(root, "rev-parse", "HEAD").stdout.strip()
    create_plan(
        root,
        {
            "run_id": "utf8-codex-integration-run",
            "objective": "record a UTF-8 Codex integration",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "codex-task",
                    "owner": "codex",
                    "ownership": ["docs/レビュー.md"],
                }
            ],
        },
    )
    diff = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root.as_posix()}",
            "diff-tree",
            "--root",
            "--binary",
            "--no-commit-id",
            "-r",
            integration_revision,
            "--",
        ],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout

    integrated = mark_integrated(
        root,
        "utf8-codex-integration-run",
        "codex-task",
        note="record UTF-8 integration evidence",
        target_ref="HEAD",
        integration_revision=integration_revision,
        source_attempt_id="codex-utf8-commit",
        verified_patch_digest=hashlib.sha256(diff).hexdigest(),
    )

    assert integrated["tasks"][0]["status"] == "INTEGRATED"


def test_commander_reissues_dependent_manifest_from_integration_revision(tmp_path):
    root, targets, revision = _repo(tmp_path)
    _manifest(root, revision, "worker-a", targets[0])
    _manifest(root, revision, "worker-b", targets[1])
    original_manifest = ".devfarm/tasks/worker-b.json"
    create_plan(
        root,
        {
            "run_id": "dependent-baseline-run",
            "objective": "advance a dependent worker baseline",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": "worker-a",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-a.json",
                    "ownership": [targets[0]],
                        "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                },
                {
                    "task_id": "worker-b",
                    "owner": "worker",
                    "dependencies": ["worker-a"],
                    "manifest_path": original_manifest,
                    "ownership": [targets[1]],
                    "assignment": {"provider_id": "cloudflare", "model_id": "@cf/meta/llama-3.1-8b-instruct"},
                },
            ],
        },
    )
    dispatch_plan(
        root,
        "dependent-baseline-run",
        providers={
            "worker-a": _WorkerProvider(
                {
                    "status": "completed",
                    "changed_files": [targets[0]],
                    "tests_run": [],
                    "tests_passed": True,
                    "known_issues": [],
                    "assumptions": [],
                    "patch": _patch(targets[0]),
                    "notes": "ready",
                }
            )
        },
    )
    verified = verify_plan(root, "dependent-baseline-run", verification_trust_level="TRUSTED_HOST_EXEC", operator_approved=True)
    integration_revision, attempt_id, digest = _integrate_worker_patch(root, "worker-a", verified["tasks"][0])
    integrated = mark_integrated(
        root,
        "dependent-baseline-run",
        "worker-a",
        note="integrated before dependent release",
        target_ref="HEAD",
        integration_revision=integration_revision,
        source_attempt_id=attempt_id,
        verified_patch_digest=digest,
    )

    dependent = integrated["tasks"][1]
    assert dependent["status"] == "READY"
    assert dependent["manifest_path"] != original_manifest
    assert (root / original_manifest).is_file()
    new_manifest = json.loads((root / dependent["manifest_path"]).read_text(encoding="utf-8"))
    assert new_manifest["base_revision"] == integration_revision
    assert original_manifest in dependent["manifest_history"]


def test_dependency_blocked_task_releases_after_dependency_recovery():
    plan = {
        "run_id": "dependency-recovery-run",
        "objective": "release a dependent task after a reworked dependency integrates",
        "base_revision": "a" * 40,
        "tasks": [
            {
                "task_id": "worker-a",
                "owner": "worker",
                "status": "INTEGRATED",
                "manifest_path": ".devfarm/tasks/worker-a.json",
                "integration_revision": "b" * 40,
                "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
            },
            {
                "task_id": "worker-b",
                "owner": "worker",
                "status": "BLOCKED",
                "block_reason": "dependency_failed",
                "dependencies": ["worker-a"],
                "manifest_path": ".devfarm/tasks/worker-b.json",
                "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
            },
        ],
    }

    refreshed = refresh_plan(plan)

    assert refreshed["tasks"][1]["status"] == "READY"
    assert "block_reason" not in refreshed["tasks"][1]


def test_dispatch_cli_preserves_assigned_provider_binding(tmp_path, monkeypatch):
    write_manifest(
        tmp_path,
        {
            "task_id": "binding-dispatch-task",
            "objective": "preserve the exact configured worker lane",
            "base_revision": "a" * 40,
            "allowed_files": ["tests/v2/worker.py"],
            "read_files": ["tests/v2/worker.py"],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["gemini"],
            "outbound_files": ["tests/v2/worker.py"],
            "requirements": [],
            "acceptance": [],
            "test_commands": ["python -m pytest tests/v2/worker.py -q"],
            "max_attempts": 1,
            "output_contract": {},
        },
    )
    create_plan(
        tmp_path,
        {
            "run_id": "binding-dispatch-run",
            "objective": "preserve the exact configured worker lane",
            "base_revision": "a" * 40,
            "tasks": [
                {
                    "task_id": "binding-dispatch-task",
                    "owner": "worker",
                    "status": "READY",
                    "manifest_path": ".devfarm/tasks/binding-dispatch-task.json",
                    "ownership": ["tests/v2/worker.py"],
                    "assignment": {
                        "provider_id": "gemini",
                        "provider_binding_id": "gemini:worker",
                        "model_id": "gemini-3.5-flash-lite",
                    },
                }
            ],
        },
    )
    provider_calls = []
    dispatch_calls = []

    def fake_build(provider_id, model_id, timeout_seconds, provider_binding_id):
        provider_calls.append((provider_id, model_id, timeout_seconds, provider_binding_id))
        return object()

    def fake_dispatch(root, run_id, **kwargs):
        dispatch_calls.append((root, run_id, kwargs))
        return {"status": "captured"}

    monkeypatch.setattr("scripts.devfarm_commander.build_cli_provider", fake_build)
    monkeypatch.setattr("scripts.devfarm_commander.dispatch_plan", fake_dispatch)

    result = dispatch_cli(tmp_path, "binding-dispatch-run", timeout_seconds=17.0, execution_boundary="in_process")

    assert result == {"status": "captured"}
    assert provider_calls == [("gemini", "gemini-3.5-flash-lite", 17.0, "gemini:worker")]
    assert dispatch_calls[0][2]["providers"]["binding-dispatch-task"] is not None


def test_dispatch_plan_explicit_local_trial_reaches_worker_assignment(tmp_path):
    root, targets, revision = _repo(tmp_path)
    write_manifest(root, {
        "task_id": "local-trial-task",
        "task_type": "worker",
        "objective": "Make one bounded local-trial change.",
        "base_revision": revision,
        "allowed_files": [targets[0]],
        "read_files": [targets[0]],
        "forbidden_files": [],
        "external_provider_allowed": True,
        "approved_provider_ids": ["ollama"],
        "outbound_files": [targets[0]],
        "requirements": ["Keep the change bounded."],
        "acceptance": ["The worker returns a patch."],
        "test_commands": ["python -m pytest tests/v2/test_devfarm_commander.py -q"],
        "max_attempts": 1,
        "output_contract": {"files": ["result.json", "patch.diff", "tests.json", "notes.md"]},
    })
    create_plan(root, {
        "run_id": "local-trial-dispatch-run",
        "objective": "Dispatch one explicit local trial.",
        "base_revision": revision,
        "tasks": [
            {
                "task_id": "local-trial-task",
                "owner": "worker",
                "manifest_path": ".devfarm/tasks/local-trial-task.json",
                "ownership": [targets[0]],
                "assignment": {"provider_id": "ollama", "model_id": "qwen3.5:9b"},
            }
        ],
    })

    captured = []

    class _Capture:
        def propose(self, _root, assignments):
            captured.extend(assignments)
            return [{
                "status": "completed",
                "attempt_id": "local-trial-attempt",
                "base_revision": revision,
                "changed_files": [targets[0]],
            }]

    provider = _WorkerProvider({"status": "completed", "changed_files": [targets[0]], "tests_run": [], "tests_passed": True, "known_issues": [], "assumptions": [], "patch": _patch(targets[0]), "notes": "ready"})
    dispatch_plan(root, "local-trial-dispatch-run", providers={"local-trial-task": provider}, orchestrator=_Capture(), local_trial=True)
    assert len(captured) == 1
    assert captured[0].local_trial is True
