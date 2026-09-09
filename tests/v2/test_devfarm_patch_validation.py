import json

import pytest

from scripts import devfarm_worker
from scripts.devfarm import DevFarmError, validate_patch
from scripts.devfarm_worker import apply_and_verify, run_worker
from tests.v2.devfarm_test_support import _RawWorkerProvider, _WorkerProvider, _workspace, _patch


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
    assert any("patch apply check failed" in issue for issue in result["known_issues"])
    assert (root / ".devfarm/results/worker-test-001/patch.diff").read_text(encoding="utf-8") == ""


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
