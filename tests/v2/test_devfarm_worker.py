import json

import pytest

from scripts.devfarm import DevFarmError, write_manifest
from scripts.devfarm_worker import run_worker
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.base import ModelProvider


class _WorkerProvider(ModelProvider):
    provider_id = "cloudflare"

    def __init__(self, output: dict):
        self.output = output

    def request(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            provider=self.provider_id,
            model="test-model",
            text_segments=[json.dumps(self.output)],
        )


def _manifest(root):
    return write_manifest(
        root,
        {
            "task_id": "worker-test-001",
            "objective": "Add a focused regression test.",
            "base_revision": "base-revision",
            "allowed_files": ["tests/v2/test_target.py"],
            "read_files": ["tests/v2/test_target.py"],
            "forbidden_files": [],
            "requirements": ["Do not change production code."],
            "acceptance": ["The focused test passes."],
            "test_commands": ["python -m pytest tests/v2/test_target.py -q"],
            "max_attempts": 1,
            "output_contract": {"files": ["result.json", "patch.diff", "tests.json", "notes.md"]},
        },
    )


def test_worker_writes_validated_result_artifacts(tmp_path):
    (tmp_path / "tests/v2").mkdir(parents=True)
    (tmp_path / "tests/v2/test_target.py").write_text("def test_target():\n    assert True\n", encoding="utf-8")
    manifest_path = _manifest(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": ["python -m pytest tests/v2/test_target.py -q"],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": "diff --git a/tests/v2/test_target.py b/tests/v2/test_target.py\n",
        "tests": {"passed": True},
        "notes": "Focused test added.",
    }

    result = run_worker(tmp_path, manifest_path, provider=_WorkerProvider(output))

    result_dir = tmp_path / ".devfarm/results/worker-test-001"
    assert result["status"] == "completed"
    assert json.loads((result_dir / "result.json").read_text(encoding="utf-8"))["tests_passed"] is True
    assert (result_dir / "patch.diff").read_text(encoding="utf-8").startswith("diff --git")
    assert json.loads((result_dir / "tests.json").read_text(encoding="utf-8"))["passed"] is True
    assert (result_dir / "notes.md").read_text(encoding="utf-8") == "Focused test added.\n"


def test_worker_rejects_model_output_outside_manifest_scope(tmp_path):
    (tmp_path / "tests/v2").mkdir(parents=True)
    (tmp_path / "tests/v2/test_target.py").write_text("def test_target():\n    assert True\n", encoding="utf-8")
    manifest_path = _manifest(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["README.md"],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
    }

    with pytest.raises(DevFarmError, match="outside manifest"):
        run_worker(tmp_path, manifest_path, provider=_WorkerProvider(output))
