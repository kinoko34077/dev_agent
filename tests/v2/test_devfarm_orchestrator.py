import json
import subprocess
import threading
import time

import pytest

from scripts.devfarm import write_manifest
from scripts.devfarm_orchestrator import (
    ConcurrencyLimitError,
    DevFarmOrchestrator,
    HostConcurrencyGovernor,
    RemoteConcurrencyGovernor,
)
from scripts.devfarm_worker import apply_and_verify
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.base import ModelProvider


class _ConcurrentProvider(ModelProvider):
    provider_id = "cloudflare"

    def __init__(self, output, tracker=None):
        self.output = output
        self.tracker = tracker

    def request(self, request: ModelRequest) -> ModelResponse:
        if self.tracker is not None:
            with self.tracker["lock"]:
                self.tracker["active"] += 1
                self.tracker["peak"] = max(self.tracker["peak"], self.tracker["active"])
            try:
                self.tracker["barrier"].wait(timeout=3)
                time.sleep(0.03)
            finally:
                with self.tracker["lock"]:
                    self.tracker["active"] -= 1
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


def _repo(tmp_path, targets):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "devfarm-tests@example.invalid")
    _git(root, "config", "user.name", "DevFarm Tests")
    for target in targets:
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_target():\n    assert True\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "proposal baseline")
    revision = _git(root, "rev-parse", "HEAD").stdout.strip()
    manifests = []
    for index, target in enumerate(targets, start=1):
        manifests.append(
            write_manifest(
                root,
                {
                    "task_id": f"orchestrator-{index}",
                    "objective": "Add a harmless focused test change.",
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
                    "max_attempts": 1,
                    "output_contract": {},
                },
            )
        )
    return root, manifests


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


def _output(path):
    return {
        "status": "completed",
        "changed_files": [path],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch(path),
        "notes": "proposal ready",
    }


def test_remote_proposals_are_bounded_without_creating_worktrees(tmp_path):
    targets = ["tests/v2/worker_a.py", "tests/v2/worker_b.py"]
    root, manifests = _repo(tmp_path, targets)
    tracker = {"lock": threading.Lock(), "barrier": threading.Barrier(2), "active": 0, "peak": 0}
    orchestrator = DevFarmOrchestrator(
        remote_governor=RemoteConcurrencyGovernor(max_inflight=2),
        host_governor=HostConcurrencyGovernor(worktree_verification_slots=1),
    )

    proposals = orchestrator.propose(
        root,
        [
            (manifests[0], _ConcurrentProvider(_output(targets[0]), tracker)),
            (manifests[1], _ConcurrentProvider(_output(targets[1]), tracker)),
        ],
    )

    assert [item["status"] for item in proposals] == ["completed", "completed"]
    assert tracker["peak"] == 2
    assert orchestrator.remote_governor.snapshot()["peak"] == 2
    assert not (root / ".devfarm/worktrees/orchestrator-1").exists()
    assert not (root / ".devfarm/worktrees/orchestrator-2").exists()


def test_run_creates_worktree_only_for_host_verification(tmp_path):
    target = "tests/v2/worker.py"
    root, manifests = _repo(tmp_path, [target])
    orchestrator = DevFarmOrchestrator(
        remote_governor=RemoteConcurrencyGovernor(max_inflight=2),
        host_governor=HostConcurrencyGovernor(worktree_verification_slots=1),
    )

    report = orchestrator.run(root, [(manifests[0], _ConcurrentProvider(_output(target)))])

    assert report.proposals[0]["status"] == "completed"
    assert report.verifications[0]["status"] == "completed"
    assert report.host_governor["worktree_verification"]["peak"] == 1
    assert (root / ".devfarm/worktrees/orchestrator-1").is_dir()
    assert "return None" not in (root / target).read_text(encoding="utf-8")


def test_disabled_host_resource_is_fail_closed():
    host = HostConcurrencyGovernor(local_model_slots=0)
    with pytest.raises(ConcurrencyLimitError, match="disabled"):
        with host.slot("local_model"):
            pass
