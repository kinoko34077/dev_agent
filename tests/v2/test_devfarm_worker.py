import json
import subprocess

import pytest

from scripts.devfarm import DevFarmError, prepare_worktree, validate_manifest, validate_patch, write_manifest
from scripts.devfarm_worker import DevFarmActivationPolicy, _input_context, _prompt, _provider, apply_and_verify, run_worker
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.base import ModelProvider
from src.dev_agent.providers.cloudflare import CloudflareWorkersAIHttpProvider
from src.dev_agent.providers.openrouter import OpenRouterHttpProvider


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


def _manifest(root, *, base_revision):
    return write_manifest(
        root,
        {
            "task_id": "worker-test-001",
            "objective": "Add a focused regression test.",
            "base_revision": base_revision,
            "allowed_files": ["tests/v2/test_target.py"],
            "read_files": ["tests/v2/test_target.py"],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["cloudflare"],
            "outbound_files": ["tests/v2/test_target.py"],
            "requirements": ["Do not change production code."],
            "acceptance": ["The focused test passes."],
            "test_commands": ["python -m pytest tests/v2/test_target.py -q"],
            "max_attempts": 1,
            "output_contract": {"files": ["result.json", "patch.diff", "tests.json", "notes.md"]},
        },
    )


def _git(*args, cwd):
    return subprocess.run(
        ["git", "-c", f"safe.directory={cwd.as_posix()}", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _workspace(tmp_path, *, prepare=True):
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    _git("init", cwd=root)
    _git("config", "user.email", "worker-tests@example.invalid", cwd=root)
    _git("config", "user.name", "Worker Tests", cwd=root)
    target = root / "tests/v2/test_target.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_target():\n    assert True\n", encoding="utf-8")
    _git("add", "tests/v2/test_target.py", cwd=root)
    _git("commit", "-m", "test baseline", cwd=root)
    revision = _git("rev-parse", "HEAD", cwd=root).stdout.strip()
    manifest_path = _manifest(root, base_revision=revision)
    if prepare:
        prepare_worktree(root, task_id="worker-test-001", branch="agent/cloudflare/worker-test-001", revision=revision)
    return root, manifest_path


def _patch(path="tests/v2/test_target.py"):
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1,2 @@\n"
        " def test_target():\n"
        "+    assert True\n"
    )


def test_devfarm_provider_uses_factory_and_explicit_activation_allowlist():
    policy = DevFarmActivationPolicy()
    assert policy.is_active("cloudflare")
    assert policy.is_active("openrouter")
    assert not policy.is_active("mistral")

    cloudflare = _provider("cloudflare", "@cf/meta/llama-3.1-8b-instruct", 4)
    assert isinstance(cloudflare, CloudflareWorkersAIHttpProvider)
    assert cloudflare.model == "@cf/meta/llama-3.1-8b-instruct"
    assert cloudflare.provider_binding_id == "cloudflare"

    openrouter = _provider("openrouter", "openrouter/free", 4)
    assert isinstance(openrouter, OpenRouterHttpProvider)
    assert openrouter.model == "openrouter/free"

    with pytest.raises(DevFarmError, match="not active"):
        _provider("mistral", "mistral-small-latest", 4)


def test_worker_prompt_makes_patch_and_test_claim_boundaries_explicit(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    prompt = _prompt(manifest, "--- BEGIN FILE tests/v2/test_target.py ---\ncontent\n--- END FILE ---")

    assert "Return exactly one JSON object" in prompt
    assert "The `patch` must be either an empty string or begin with `diff --git`" in prompt
    assert "Do not use Markdown fences, `*** Begin Patch`, prose" in prompt
    assert "`tests_run` is only a proposed command list" in prompt


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


def test_worker_requires_existing_clean_worktree_at_manifest_revision(tmp_path):
    root, manifest_path = _workspace(tmp_path, prepare=False)
    with pytest.raises(DevFarmError, match="worktree"):
        run_worker(root, manifest_path, provider=_WorkerProvider({}))

    root, manifest_path = _workspace(tmp_path / "dirty", prepare=True)
    workspace = root / ".devfarm/worktrees/worker-test-001"
    (workspace / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(DevFarmError, match="dirty"):
        run_worker(root, manifest_path, provider=_WorkerProvider({}))

    root, manifest_path = _workspace(tmp_path / "mismatch", prepare=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["base_revision"] = "0" * 40
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DevFarmError, match="base_revision"):
        run_worker(root, manifest_path, provider=_WorkerProvider({}))


def test_worker_reads_only_approved_outbound_files_and_rejects_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("API_KEY = 'sk-secret-value'\n", encoding="utf-8")
    link = workspace / "input.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows runner")
    manifest = validate_manifest(
        {
            "task_id": "symlink-test-001",
            "objective": "test",
            "base_revision": "0" * 40,
            "allowed_files": ["input.py"],
            "read_files": ["input.py"],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["cloudflare"],
            "outbound_files": ["input.py"],
            "requirements": [],
            "acceptance": [],
            "test_commands": [],
            "max_attempts": 1,
            "output_contract": {},
        }
    )
    with pytest.raises(DevFarmError, match="symlink|outside"):
        _input_context(workspace, manifest)


def test_worker_rejects_secret_in_outbound_source(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "input.py"
    source.write_text("api_key = 'gsk_12345678901234567890'\n", encoding="utf-8")
    manifest = validate_manifest(
        {
            "task_id": "secret-test-001",
            "objective": "test",
            "base_revision": "0" * 40,
            "allowed_files": ["input.py"],
            "read_files": ["input.py"],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["cloudflare"],
            "outbound_files": ["input.py"],
            "requirements": [],
            "acceptance": [],
            "test_commands": [],
            "max_attempts": 1,
            "output_contract": {},
        }
    )
    with pytest.raises(DevFarmError, match="secret"):
        _input_context(workspace, manifest)


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
    from scripts import devfarm_worker

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


def test_manifest_allows_only_bounded_host_test_command_shapes():
    manifest = {
        "task_id": "command-shape-001",
        "objective": "test",
        "base_revision": "0" * 40,
        "allowed_files": ["tests/v2/test_target.py"],
        "read_files": ["tests/v2/test_target.py"],
        "forbidden_files": [],
        "external_provider_allowed": True,
        "approved_provider_ids": ["cloudflare"],
        "outbound_files": ["tests/v2/test_target.py"],
        "requirements": [],
        "acceptance": [],
        "test_commands": ["python -m pytest tests/v2/test_target.py -q"],
        "max_attempts": 1,
        "output_contract": {},
    }
    assert validate_manifest(manifest)["test_commands"] == ["python -m pytest tests/v2/test_target.py -q"]

    manifest["test_commands"] = ["python -c 'print(1)'"]
    with pytest.raises(DevFarmError, match="test_commands"):
        validate_manifest(manifest)

    manifest["test_commands"] = ["python -m pytest tests/v2/test_target.py -q; whoami"]
    with pytest.raises(DevFarmError, match="test_commands"):
        validate_manifest(manifest)


def test_host_verification_applies_patch_only_in_worker_worktree(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    output = {
        "status": "completed",
        "changed_files": ["README.md"],
        "tests_run": ["python -c 'print(should-not-run)'"] ,
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
