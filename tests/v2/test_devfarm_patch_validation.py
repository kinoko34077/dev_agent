import json
import subprocess
import sys

import pytest

from scripts import devfarm_worker
from scripts.devfarm import DevFarmError, validate_patch
from scripts.devfarm_worker import HostVerificationRunner, apply_and_verify, run_worker
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.base import ModelProvider
from tests.v2.devfarm_test_support import _RawWorkerProvider, _WorkerProvider, _workspace, _patch


class _CapturingWorkerProvider(_WorkerProvider):
    def __init__(self, output):
        super().__init__(output)
        self.request_text = ""

    def request(self, request: ModelRequest) -> ModelResponse:
        self.request_text = request.messages[-1]["content"]
        return super().request(request)


class _MeasuredWorkerProvider(ModelProvider):
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
            usage={"total_tokens": 7, "prompt_tokens": 3, "completion_tokens": 4},
        )


def test_host_verification_runner_sanitizes_environment_and_records_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "should-not-cross-host-boundary")
    result = HostVerificationRunner(timeout_seconds=5, max_output_bytes=4096).run(
        [
            sys.executable,
            "-c",
            "import os; print(os.getenv('GROQ_API_KEY')); print(os.getenv('HOME')); print(os.getenv('DEV_AGENT_HOST_VERIFICATION'))",
        ],
        cwd=tmp_path,
    )

    assert result["returncode"] == 0
    assert "should-not-cross-host-boundary" not in result["stdout"]
    assert "None" in result["stdout"]
    assert "DEV_AGENT_HOST_VERIFICATION" not in result["stdout"] or "1" in result["stdout"]
    assert result["containment"] == {
        "environment": "sanitized_allowlist",
        "home": "temporary",
        "process_tree": "terminated_on_timeout",
        "network": "not_isolated",
        "sandbox": "not_provided",
    }


def test_host_verification_runner_bounds_output(tmp_path):
    result = HostVerificationRunner(timeout_seconds=5, max_output_bytes=64).run(
        [sys.executable, "-c", "print('x' * 10000)"],
        cwd=tmp_path,
    )

    assert result["returncode"] == 0
    assert result["output_truncated"] is True
    assert len(result["stdout"].encode("utf-8")) <= 64


def test_host_verification_runner_terminates_timed_out_process(tmp_path):
    result = HostVerificationRunner(timeout_seconds=0.1, max_output_bytes=1024).run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
    )

    assert result["timed_out"] is True
    assert result["returncode"] is None


def test_worker_writes_validated_result_artifacts(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": ["python -m pytest tests/v2/test_target.py -q"],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch(),
        "tests": {"passed": True},
        "notes": "Focused test added.",
    }

    result = run_worker(root, manifest_path, provider=_WorkerProvider(output))

    result_dir = root / ".devfarm/results/worker-test-001"
    assert result["status"] == "completed"
    stored = json.loads((result_dir / "result.json").read_text(encoding="utf-8"))
    assert stored["tests_passed"] is False
    assert stored["model_claims"]["tests_passed"] is True
    assert stored["host_verified_tests"] == []
    assert (result_dir / "patch.diff").read_text(encoding="utf-8").startswith("diff --git")
    tests_artifact = json.loads((result_dir / "tests.json").read_text(encoding="utf-8"))
    assert tests_artifact["model_claims"]["tests_passed"] is True
    assert tests_artifact["host_verified_tests"] == []
    assert (result_dir / "notes.md").read_text(encoding="utf-8") == "Focused test added.\n"


def test_worker_records_host_measurements_and_updates_acceptance_after_verification(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch(),
        "notes": "proposal ready",
    }

    proposed = run_worker(root, manifest_path, provider=_MeasuredWorkerProvider(output))

    metrics = proposed["worker_metrics"]
    assert metrics["provider_id"] == "cloudflare"
    assert metrics["provider_binding_id"] == "cloudflare"
    assert metrics["model_id"] == "@cf/meta/llama-3.1-8b-instruct"
    assert metrics["intelligence_tier"] == "L1"
    assert isinstance(metrics["request_id"], str) and metrics["request_id"]
    assert metrics["elapsed_ms"] >= 0
    assert metrics["usage"] == {"total_tokens": 7, "prompt_tokens": 3, "completion_tokens": 4}
    assert metrics["attempt_count"] == 1
    assert metrics["host_verified"] is False
    assert metrics["result_accepted"] is None

    verified = apply_and_verify(root, manifest_path)

    verified_metrics = verified["worker_metrics"]
    assert verified_metrics["host_verified"] is True
    assert verified_metrics["host_verified_test_count"] == 1
    assert verified_metrics["host_tests_passed"] is True
    assert verified_metrics["result_accepted"] is True
    stored = json.loads((root / ".devfarm/results/worker-test-001/result.json").read_text(encoding="utf-8"))
    assert stored["worker_metrics"]["result_accepted"] is True


def test_worker_retries_keep_immutable_attempt_artifacts(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch(),
        "notes": "proposal ready",
    }

    first = run_worker(root, manifest_path, provider=_WorkerProvider(output))
    second = run_worker(root, manifest_path, provider=_WorkerProvider(output))

    assert first["attempt_id"] != second["attempt_id"]
    result_dir = root / ".devfarm/results/worker-test-001"
    for attempt_id in (first["attempt_id"], second["attempt_id"]):
        attempt_result = result_dir / "attempts" / attempt_id / "result.json"
        assert attempt_result.is_file()
        assert json.loads(attempt_result.read_text(encoding="utf-8"))["attempt_id"] == attempt_id
    history = (result_dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["attempt_id"] for line in history] == [first["attempt_id"], second["attempt_id"]]


def test_host_verification_reads_patch_from_the_selected_attempt(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch(),
        "notes": "proposal ready",
    }

    proposed = run_worker(root, manifest_path, provider=_WorkerProvider(output))
    result_dir = root / ".devfarm/results/worker-test-001"
    # The root result/patch files are only the latest projection.  A stale or
    # concurrently replaced projection must not change which attempt is
    # applied during host verification.
    (result_dir / "patch.diff").write_text("not a unified diff\n", encoding="utf-8")

    verified = apply_and_verify(root, manifest_path)

    assert proposed["attempt_id"]
    assert verified["status"] == "completed"
    assert verified["attempt_id"] == proposed["attempt_id"]
    assert verified["tests_passed"] is True


def test_host_verification_rejects_missing_selected_attempt_artifact(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch(),
        "notes": "proposal ready",
    }

    proposed = run_worker(root, manifest_path, provider=_WorkerProvider(output))
    attempt_patch = (
        root
        / ".devfarm/results/worker-test-001/attempts"
        / proposed["attempt_id"]
        / "patch.diff"
    )
    attempt_patch.unlink()

    with pytest.raises(DevFarmError, match="artifact is missing"):
        apply_and_verify(root, manifest_path)


def test_worker_records_nonsemantic_final_newline_normalization(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch().rstrip("\n"),
        "notes": "proposal ready",
    }

    result = run_worker(root, manifest_path, provider=_WorkerProvider(output))

    assert result["status"] == "completed"
    assert result["worker_metrics"]["patch_normalizations"] == ["appended_final_newline"]
    assert (root / ".devfarm/results/worker-test-001/patch.diff").read_bytes().endswith(b"\n")


def test_worker_normalizes_bounded_model_status_aliases_without_trusting_claims(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "success",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch(),
        "notes": "proposal ready",
    }

    result = run_worker(root, manifest_path, provider=_WorkerProvider(output))

    assert result["status"] == "completed"
    assert result["model_claims"]["status"] == "success"
    assert result["tests_passed"] is False


def test_worker_preserves_valid_proposal_when_notes_are_not_text(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch(),
        "notes": ["proposal", {"host_verified": False}],
    }

    result = run_worker(root, manifest_path, provider=_WorkerProvider(output))

    assert result["status"] == "completed"
    notes = (root / ".devfarm/results/worker-test-001/notes.md").read_text(encoding="utf-8")
    assert notes.startswith("[MODEL_NOTES_NORMALIZED type=list]")
    assert '"host_verified": false' in notes


def test_worker_records_model_output_outside_manifest_scope_as_failed_artifact(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["README.md"],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": "diff --git a/README.md b/README.md\n",
    }

    result = run_worker(root, manifest_path, provider=_WorkerProvider(output))

    assert result["status"] == "failed"
    assert any("outside manifest" in issue for issue in result["known_issues"])
    assert result["changed_files"] == []
    assert (root / ".devfarm/results/worker-test-001/patch.diff").read_text(encoding="utf-8") == ""


def test_worker_records_malformed_model_patch_as_failed_artifact(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": [],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": "not a unified diff",
        "notes": "model returned an invalid proposal",
    }

    result = run_worker(root, manifest_path, provider=_WorkerProvider(output))

    assert result["status"] == "failed"
    assert any("unified diff" in issue for issue in result["known_issues"])
    assert json.loads((root / ".devfarm/results/worker-test-001/tests.json").read_text(encoding="utf-8"))["host_verified_tests"] == []


def test_worker_records_malformed_model_json_as_failed_artifact(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    malformed = '{"status":"completed","patch":"line\nbreak"}'

    result = run_worker(root, manifest_path, provider=_RawWorkerProvider(malformed))

    assert result["status"] == "failed"
    assert any("JSON" in issue for issue in result["known_issues"])
    result_dir = root / ".devfarm/results/worker-test-001"
    assert json.loads((result_dir / "result.json").read_text(encoding="utf-8"))["changed_files"] == []
    assert (result_dir / "patch.diff").read_text(encoding="utf-8") == ""


def test_worker_rejects_patch_that_is_path_safe_but_not_applicable(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch().replace("@@ -1,2 +1,3 @@", "@@ -1,6 +1,7 @@"),
        "notes": "invalid hunk proposal",
    }

    result = run_worker(root, manifest_path, provider=_WorkerProvider(output))

    assert result["status"] == "failed"
    assert any("hunk line counts" in issue for issue in result["known_issues"])
    assert (root / ".devfarm/results/worker-test-001/patch.diff").read_text(encoding="utf-8") == ""


def test_patch_validation_rejects_invalid_hunk_header(tmp_path):
    _root, manifest_path = _workspace(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    patch = _patch().replace("@@ -1,2 +1,3 @@", "@@ -1,2 +1,4 @@")

    with pytest.raises(DevFarmError, match="hunk line counts"):
        validate_patch(patch, manifest=manifest)


def test_worker_proposal_without_worktree_is_verified_after_late_worktree_creation(tmp_path):
    root, manifest_path = _workspace(tmp_path, prepare=False)
    output = {
        "status": "completed",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch(),
        "notes": "proposal ready",
    }

    proposed = run_worker(root, manifest_path, provider=_WorkerProvider(output))
    assert proposed["status"] == "completed"
    assert not (root / ".devfarm/worktrees/worker-test-001").exists()

    verified = apply_and_verify(root, manifest_path)
    assert verified["status"] == "completed"
    assert verified["tests_passed"] is True


def test_worker_proposal_reads_the_manifest_commit_after_repository_head_advances(tmp_path):
    root, manifest_path = _workspace(tmp_path, prepare=False)
    target = root / "tests/v2/test_target.py"
    baseline = target.read_text(encoding="utf-8")

    target.write_text(baseline.replace("assert True", "assert False"), encoding="utf-8")
    _git = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "add", "tests/v2/test_target.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", "commit", "-m", "advance repository head"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )

    provider = _CapturingWorkerProvider(
        {
            "status": "completed",
            "changed_files": ["tests/v2/test_target.py"],
            "tests_run": [],
            "tests_passed": True,
            "known_issues": [],
            "assumptions": [],
            "patch": _patch(),
            "notes": "proposal from the pinned commit",
        }
    )
    proposed = run_worker(root, manifest_path, provider=provider)

    assert proposed["status"] == "completed"
    assert "assert True" in provider.request_text
    assert "assert False" not in provider.request_text
    verified = apply_and_verify(root, manifest_path)
    assert verified["status"] == "completed"
    assert verified["tests_passed"] is True
    assert (root / ".devfarm/worktrees/worker-test-001").is_dir()


def test_worker_does_not_accept_completed_result_without_patch(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": [],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": "",
        "notes": "no change",
    }

    result = run_worker(root, manifest_path, provider=_WorkerProvider(output))

    assert result["status"] == "failed"
    assert any("non-empty patch" in issue for issue in result["known_issues"])


@pytest.mark.parametrize(
    "patch",
    [
        "diff --git a/README.md b/README.md\n",
        "GIT binary patch\n",
        "diff --git a/tests/v2/test_target.py b/tests/v2/escape.py\n",
        "diff --git a/tests/v2/test_target.py b/tests/v2/test_target.py\nnew file mode 120000\n",
        "diff --git a/tests/v2/test_target.py b/tests/v2/test_target.py\nnew file mode 160000\n",
    ],
)
def test_patch_validation_uses_actual_paths_and_rejects_unsafe_changes(tmp_path, patch):
    _root, manifest_path = _workspace(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with pytest.raises(DevFarmError, match="patch|allowed|binary|symlink|submodule|mode"):
        validate_patch(patch, manifest=manifest)


def test_worker_rejects_oversized_patch_without_truncating_artifact(tmp_path, monkeypatch):
    root, manifest_path = _workspace(tmp_path)
    monkeypatch.setattr(devfarm_worker, "MAX_OUTPUT_TEXT_CHARS", 32)
    output = {
        "status": "completed",
        "changed_files": [],
        "tests_run": [],
        "tests_passed": False,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch() + "x" * 100,
        "notes": "",
    }
    with pytest.raises(DevFarmError, match="patch"):
        devfarm_worker._write_auxiliary_artifacts(root, "worker-test-001", output)
    assert not (root / ".devfarm/results/worker-test-001/patch.diff").exists()


def test_host_verification_applies_patch_only_in_worker_worktree(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["README.md"],
        "tests_run": ["python -c 'print(should-not-run)'"],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": (
            "diff --git a/tests/v2/test_target.py b/tests/v2/test_target.py\n"
            "--- a/tests/v2/test_target.py\n"
            "+++ b/tests/v2/test_target.py\n"
            "@@ -1,2 +1,5 @@\n"
            " def test_target():\n"
            "     assert True\n"
            "+\n"
            "+def test_worker_patch():\n"
            "+    assert True\n"
        ),
        "notes": "added a focused test",
    }
    run_worker(root, manifest_path, provider=_WorkerProvider(output))

    verified = apply_and_verify(root, manifest_path)

    assert verified["status"] == "completed"
    assert verified["tests_passed"] is True
    assert verified["host_verified_tests"][0]["exit_code"] == 0
    assert verified["model_claims"]["tests_passed"] is True
    worker_file = root / ".devfarm/worktrees/worker-test-001/tests/v2/test_target.py"
    assert "test_worker_patch" in worker_file.read_text(encoding="utf-8")
    assert "test_worker_patch" not in (root / "tests/v2/test_target.py").read_text(encoding="utf-8")

    stored = json.loads((root / ".devfarm/results/worker-test-001/result.json").read_text(encoding="utf-8"))
    assert stored["host_verified_tests"][0]["command"] == "python -m pytest tests/v2/test_target.py -q"


def test_host_verification_backfills_missing_notes_artifact(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": [],
        "tests_passed": False,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch(),
        "notes": "proposal ready",
    }
    run_worker(root, manifest_path, provider=_WorkerProvider(output))
    (root / ".devfarm/results/worker-test-001/notes.md").unlink()
    attempts_notes = next(
        (root / ".devfarm/results/worker-test-001/attempts").glob("*/notes.md")
    )
    attempts_notes.unlink()

    verified = apply_and_verify(root, manifest_path)

    assert verified["status"] == "completed"
    assert (root / ".devfarm/results/worker-test-001/notes.md").read_text(encoding="utf-8") == (
        "Host verification completed; no model notes artifact was available.\n"
    )
