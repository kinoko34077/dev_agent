import json
import subprocess
import threading
import time

import pytest

import scripts.devfarm_orchestrator as orchestrator_module
from scripts.devfarm import write_manifest
from scripts.devfarm_repository import read_json
from scripts.devfarm_orchestrator import (
    ConcurrencyLimitError,
    DevFarmOrchestrator,
    HostConcurrencyGovernor,
    RemoteConcurrencyGovernor,
    WorkerAssignment,
)
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.fake.provider import FakeProvider


def test_orchestrator_uses_shared_repository_json_reader():
    assert orchestrator_module.read_json is read_json


class _ConcurrentProvider(FakeProvider):
    provider_id = "cloudflare"
    provider_binding_id = "cloudflare"
    model_id = "@cf/meta/llama-3.1-8b-instruct"
    intelligence_tier = "L1"

    def __init__(self, output, tracker=None, provider_id="cloudflare"):
        self.output = output
        self.tracker = tracker
        self.provider_id = provider_id
        if provider_id == "gemini":
            self.provider_binding_id = "gemini:worker"
            self.model_id = "gemini-3.5-flash-lite"
        else:
            self.provider_binding_id = "cloudflare"
            self.model_id = "@cf/meta/llama-3.1-8b-instruct"

    def request(self, request: ModelRequest) -> ModelResponse:
        if self.tracker is not None:
            with self.tracker["lock"]:
                self.tracker["active"] += 1
                self.tracker["peak"] = max(self.tracker["peak"], self.tracker["active"])
            try:
                if self.tracker.get("barrier") is not None:
                    self.tracker["barrier"].wait(timeout=3)
                time.sleep(0.03)
            finally:
                with self.tracker["lock"]:
                    self.tracker["active"] -= 1
        return ModelResponse(
            provider=self.provider_id,
            model=self.model_id,
            text_segments=[json.dumps(self.output)],
        )


class _BoundaryFailureProvider(_ConcurrentProvider):
    def request(self, request: ModelRequest) -> ModelResponse:
        raise RuntimeError("simulated adapter boundary failure")


class _HostDispatch:
    execution_boundary = "host_process"
    last_transport_category = None

    def __init__(self, provider):
        self.provider = provider
        self.calls = 0
        self.provider_identity = {
            "provider_id": provider.provider_id,
            "provider_binding_id": provider.provider_binding_id,
            "model_id": provider.model_id,
            "intelligence_tier": provider.intelligence_tier,
        }

    def request(self, _request):
        self.calls += 1
        return ModelResponse(
            provider=self.provider.provider_id,
            model=self.provider.model_id,
            text_segments=[json.dumps(self.provider.output)],
        )


class _FailingHostDispatch(_HostDispatch):
    def request(self, _request):
        self.calls += 1
        error = ProviderError(
            "Host provider runtime rejected the dispatch",
            category="host_configuration",
            retryable=False,
        )
        error.host_failure_category = "host_runtime_failure"
        error.host_failure_type = "RuntimeError"
        raise error


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-c", f"safe.directory={cwd.as_posix()}", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _repo(tmp_path, targets, provider_ids=None):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "devfarm-tests@example.invalid")
    _git(root, "config", "user.name", "DevFarm Tests")
    for target in targets:
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("def test_target():\n    assert True\n", encoding="utf-8")
    baseline = root / "tests/v2/baseline.py"
    baseline.write_text("def test_baseline():\n    assert True\n", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "proposal baseline")
    revision = _git(root, "rev-parse", "HEAD").stdout.strip()
    selected_providers = list(provider_ids or ["cloudflare"] * len(targets))
    assert len(selected_providers) == len(targets)
    manifests = []
    for index, target in enumerate(targets, start=1):
        provider_id = selected_providers[index - 1]
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
                    "approved_provider_ids": [provider_id],
                    "outbound_files": [target],
                    "requirements": [],
                    "acceptance": ["focused test passes"],
                    "test_commands": [f"python -m pytest {target} tests/v2/baseline.py -q"],
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
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
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


def test_unexpected_worker_boundary_failure_is_persisted_per_attempt(tmp_path):
    target = "tests/v2/worker.py"
    root, manifests = _repo(tmp_path, [target])
    orchestrator = DevFarmOrchestrator(
        remote_governor=RemoteConcurrencyGovernor(max_inflight=1),
    )

    proposals = orchestrator.propose(root, [(manifests[0], _BoundaryFailureProvider(_output(target)))])

    assert proposals[0]["status"] == "failed"
    attempt_id = proposals[0]["attempt_id"]
    result_path = root / ".devfarm" / "results" / "orchestrator-1" / "attempts" / attempt_id / "result.json"
    assert result_path.is_file()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["base_revision"] == proposals[0]["base_revision"]
    assert result["worker_metrics"]["boundary_exception"] is True
    assert "simulated adapter boundary failure" in result["known_issues"][0]


def test_run_creates_worktree_only_for_host_verification(tmp_path):
    target = "tests/v2/worker.py"
    root, manifests = _repo(tmp_path, [target])
    orchestrator = DevFarmOrchestrator(
        remote_governor=RemoteConcurrencyGovernor(max_inflight=2),
        host_governor=HostConcurrencyGovernor(worktree_verification_slots=1),
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )

    report = orchestrator.run(root, [(manifests[0], _ConcurrentProvider(_output(target)))])

    assert report.proposals[0]["status"] == "completed"
    assert report.verifications[0]["status"] == "completed"
    assert report.host_governor["worktree_verification"]["peak"] == 1
    assert (root / ".devfarm/worktrees/orchestrator-1").is_dir()
    assert "return None" not in (root / target).read_text(encoding="utf-8")


def test_orchestrator_preserves_host_dispatch_boundary_for_worker_assignment(tmp_path):
    target = "tests/v2/worker.py"
    root, manifests = _repo(tmp_path, [target])
    provider = _ConcurrentProvider(_output(target))
    host_dispatch = _HostDispatch(provider)
    orchestrator = DevFarmOrchestrator(remote_governor=RemoteConcurrencyGovernor(max_inflight=1))

    proposals = orchestrator.propose(
        root,
        [WorkerAssignment(manifests[0], provider, host_dispatch=host_dispatch)],
    )

    assert proposals[0]["status"] == "completed"
    assert host_dispatch.calls == 1
    assert provider.tracker is None


def test_worker_metrics_preserve_host_failure_category_separately_from_transport(tmp_path):
    target = "tests/v2/worker.py"
    root, manifests = _repo(tmp_path, [target])
    provider = _ConcurrentProvider(_output(target))
    host_dispatch = _FailingHostDispatch(provider)

    orchestrator = DevFarmOrchestrator(remote_governor=RemoteConcurrencyGovernor(max_inflight=1))
    proposals = orchestrator.propose(
        root,
        [WorkerAssignment(manifests[0], provider, host_dispatch=host_dispatch)],
    )

    assert proposals[0]["status"] == "failed"
    metrics = proposals[0]["worker_metrics"]
    assert metrics["provider_failure_category"] == "host_configuration"
    assert metrics["transport_failure_category"] is None
    assert metrics["host_failure_category"] == "host_runtime_failure"
    assert metrics["host_failure_type"] == "RuntimeError"


def test_disabled_host_resource_is_fail_closed():
    host = HostConcurrencyGovernor(local_model_slots=0)
    with pytest.raises(ConcurrencyLimitError, match="disabled"):
        with host.slot("local_model"):
            pass


def test_disabled_remote_resource_is_fail_closed(tmp_path):
    root, manifests = _repo(tmp_path, ["tests/v2/worker.py"])
    orchestrator = DevFarmOrchestrator(remote_governor=RemoteConcurrencyGovernor(max_inflight=0))
    with pytest.raises(ConcurrencyLimitError, match="remote inference"):
        orchestrator.propose(root, [(manifests[0], _ConcurrentProvider(_output("tests/v2/worker.py")))])


def test_remote_binding_limit_serializes_same_quota_identity(tmp_path):
    targets = ["tests/v2/worker_a.py", "tests/v2/worker_b.py"]
    root, manifests = _repo(tmp_path, targets)
    tracker = {"lock": threading.Lock(), "barrier": None, "active": 0, "peak": 0}
    orchestrator = DevFarmOrchestrator(
        remote_governor=RemoteConcurrencyGovernor(max_inflight=2, per_binding_limits={"cloudflare": 1}),
        host_governor=HostConcurrencyGovernor(worktree_verification_slots=1),
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )

    proposals = orchestrator.propose(
        root,
        [
            (manifests[0], _ConcurrentProvider(_output(targets[0]), tracker)),
            (manifests[1], _ConcurrentProvider(_output(targets[1]), tracker)),
        ],
    )

    assert [item["status"] for item in proposals] == ["completed", "completed"]
    assert tracker["peak"] == 1
    assert orchestrator.remote_governor.snapshot()["per_binding"]["cloudflare"]["peak"] == 1


def test_remote_binding_limits_allow_independent_quota_identities_in_parallel(tmp_path):
    targets = ["tests/v2/worker_a.py", "tests/v2/worker_b.py"]
    root, manifests = _repo(tmp_path, targets, provider_ids=["cloudflare", "gemini"])
    tracker = {"lock": threading.Lock(), "barrier": threading.Barrier(2), "active": 0, "peak": 0}
    orchestrator = DevFarmOrchestrator(
        remote_governor=RemoteConcurrencyGovernor(max_inflight=2, per_binding_limits={"cloudflare": 1, "gemini:worker": 1}),
        host_governor=HostConcurrencyGovernor(worktree_verification_slots=1),
        verification_trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )

    proposals = orchestrator.propose(
        root,
        [
            (manifests[0], _ConcurrentProvider(_output(targets[0]), tracker)),
            (manifests[1], _ConcurrentProvider(_output(targets[1]), tracker, provider_id="gemini")),
        ],
    )

    assert [item["status"] for item in proposals] == ["completed", "completed"]
    assert tracker["peak"] == 2
    snapshot = orchestrator.remote_governor.snapshot()["per_binding"]
    assert snapshot["cloudflare"]["peak"] == 1
    assert snapshot["gemini:worker"]["peak"] == 1
