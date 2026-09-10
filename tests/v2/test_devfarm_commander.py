import json
import subprocess

import pytest

from scripts.devfarm import DevFarmError, write_manifest
from scripts.devfarm import main as devfarm_main
from scripts.devfarm_commander import (
    CommanderPlanStore,
    create_plan,
    dispatch_plan,
    mark_integrated,
    reassign_task,
    resume_plan,
    verify_plan,
)
from scripts.devfarm_orchestrator import DevFarmOrchestrator, HostConcurrencyGovernor, RemoteConcurrencyGovernor
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.base import ModelProvider


class _WorkerProvider(ModelProvider):
    provider_id = "cloudflare"

    def __init__(self, output):
        self.output = output

    def request(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            provider=self.provider_id,
            model="test-model",
            text_segments=[json.dumps(self.output)],
        )


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
            "test_commands": [f"python -m pytest {target} -q"],
            "max_attempts": 2,
            "output_contract": {},
        },
    )


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
                    "assignment": {"provider_id": "cloudflare", "model_id": "test-model"},
                },
                {
                    "task_id": "worker-b",
                    "owner": "worker",
                    "manifest_path": ".devfarm/tasks/worker-b.json",
                    "ownership": [targets[1]],
                    "assignment": {"provider_id": "cloudflare", "model_id": "test-model"},
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
    )
    proposed = dispatch_plan(root, "commander-run-001", providers=providers, orchestrator=orchestrator)
    assert {task["status"] for task in proposed["tasks"][:2]} == {"PROPOSED"}
    assert not (root / ".devfarm/worktrees/worker-a").exists()
    assert not (root / ".devfarm/worktrees/worker-b").exists()

    verified = verify_plan(root, "commander-run-001", orchestrator=orchestrator)
    assert {task["status"] for task in verified["tasks"][:2]} == {"HOST_VERIFIED"}
    resumed = resume_plan(root, "commander-run-001")
    assert resumed["tasks"][2]["status"] == "PLANNED"
    assert len(resumed["results"]) >= 4

    mark_integrated(root, "commander-run-001", "worker-a", note="Codex reviewed the verified patch")
    still_waiting = CommanderPlanStore(root).load("commander-run-001")
    assert still_waiting["tasks"][2]["status"] == "PLANNED"
    mark_integrated(root, "commander-run-001", "worker-b", note="Codex reviewed the verified patch")
    released = CommanderPlanStore(root).load("commander-run-001")
    assert released["tasks"][2]["status"] == "READY"
    mark_integrated(root, "commander-run-001", "codex-review", note="Codex completed the integration review")
    final = CommanderPlanStore(root).load("commander-run-001")
    assert final["tasks"][0]["status"] == "INTEGRATED"
    assert final["tasks"][2]["status"] == "INTEGRATED"


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
                    "assignment": {"provider_id": "cloudflare", "model_id": "test-model"},
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
