import json
import subprocess

import pytest

from scripts.devfarm import DevFarmError, validate_manifest
from scripts.devfarm_worker import DevFarmActivationPolicy, _input_context, _prompt, _provider, apply_and_verify, run_worker
from src.dev_agent.providers.cloudflare import CloudflareWorkersAIHttpProvider
from src.dev_agent.providers.gemini import GeminiHttpProvider
from src.dev_agent.providers.openrouter import OpenRouterHttpProvider
from tests.v2.devfarm_test_support import _RawWorkerProvider, _WorkerProvider, _workspace, _patch


def test_devfarm_provider_uses_factory_and_explicit_activation_allowlist():
    policy = DevFarmActivationPolicy()
    assert not policy.is_active("cloudflare")
    assert policy.is_active("cloudflare", "@cf/meta/llama-3.1-8b-instruct")
    assert not policy.is_active("cloudflare", "arbitrary-unqualified-model")
    assert not policy.is_active("gemini")
    assert policy.is_active("gemini", "gemini-3.5-flash-lite")
    assert not policy.is_active("gemini", "gemini-3.8-flash")
    assert not policy.is_active("openrouter")
    assert not policy.is_active("mistral")

    cloudflare = _provider("cloudflare", "@cf/meta/llama-3.1-8b-instruct", 4)
    assert isinstance(cloudflare, CloudflareWorkersAIHttpProvider)
    assert cloudflare.model == "@cf/meta/llama-3.1-8b-instruct"
    assert cloudflare.provider_binding_id == "cloudflare"

    openrouter = _provider("openrouter", "openrouter/free", 4)
    assert isinstance(openrouter, OpenRouterHttpProvider)
    assert openrouter.model == "openrouter/free"

    gemini = _provider("gemini", "gemini-3.5-flash-lite", 4)
    assert isinstance(gemini, GeminiHttpProvider)
    assert gemini.model == "gemini-3.5-flash-lite"
    assert gemini.provider_binding_id == "gemini:worker"
    assert gemini.intelligence_tier == "L1"

    with pytest.raises(DevFarmError, match="not active"):
        _provider("mistral", "mistral-small-latest", 4)


def test_worker_prompt_makes_patch_and_test_claim_boundaries_explicit(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    prompt = _prompt(manifest, "--- BEGIN FILE tests/v2/test_target.py ---\ncontent\n--- END FILE ---")

    assert "Return exactly one JSON object" in prompt
    assert "The `patch` must be either an empty string or begin with `diff --git`" in prompt
    assert "Every diff header must use `diff --git a/relative/path b/relative/path`" in prompt
    assert "Do not include trailing whitespace" in prompt
    assert "Do not use Markdown fences, `*** Begin Patch`, prose" in prompt
    assert "`tests_run` is only a proposed command list" in prompt


def test_worker_proposal_does_not_require_a_worktree_but_binds_clean_inputs(tmp_path):
    root, manifest_path = _workspace(tmp_path, prepare=False)
    output = {
        "status": "completed",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": [],
        "tests_passed": False,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch(),
        "notes": "proposal only",
    }
    result = run_worker(root, manifest_path, provider=_WorkerProvider(output))
    assert result["status"] == "completed"
    assert not (root / ".devfarm/worktrees/worker-test-001").exists()

    root, manifest_path = _workspace(tmp_path / "dirty", prepare=False)
    (root / "tests/v2/test_target.py").write_text("dirty\n", encoding="utf-8")
    # Proposal input is pinned to the commit object, so unrelated uncommitted
    # checkout changes cannot alter what is sent to the external Worker.
    result = run_worker(root, manifest_path, provider=_WorkerProvider(output))
    assert result["status"] == "completed"

    root, manifest_path = _workspace(tmp_path / "mismatch", prepare=True)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["base_revision"] = "0" * 40
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DevFarmError, match="base_revision"):
        run_worker(root, manifest_path, provider=_WorkerProvider({}))


def test_host_verification_rejects_dirty_existing_worktree(tmp_path):
    root, manifest_path = _workspace(tmp_path, prepare=True)
    run_worker(
        root,
        manifest_path,
        provider=_WorkerProvider(
            {
                "status": "completed",
                "changed_files": ["tests/v2/test_target.py"],
                "tests_run": [],
                "tests_passed": False,
                "known_issues": [],
                "assumptions": [],
                "patch": _patch(),
                "notes": "proposal ready",
            }
        ),
    )
    workspace = root / ".devfarm/worktrees/worker-test-001"
    (workspace / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(DevFarmError, match="dirty"):
        apply_and_verify(root, manifest_path)


def test_worker_reads_only_approved_outbound_files_and_rejects_symlink_escape(tmp_path):
    workspace, manifest_path = _workspace(tmp_path, prepare=False)
    outside = tmp_path / "outside.txt"
    outside.write_text("API_KEY = 'sk-secret-value'\n", encoding="utf-8")
    link = workspace / "tests/v2/test_target.py"
    try:
        link.unlink()
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows runner")
    subprocess.run(
        ["git", "-c", f"safe.directory={workspace.as_posix()}", "add", "-A"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-c", f"safe.directory={workspace.as_posix()}", "-c", "user.email=worker-tests@example.invalid", "-c", "user.name=Worker Tests", "commit", "-m", "symlink fixture"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["base_revision"] = subprocess.run(
        ["git", "-c", f"safe.directory={workspace.as_posix()}", "rev-parse", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DevFarmError, match="symlink|outside"):
        _input_context(workspace, manifest)


def test_worker_rejects_secret_in_outbound_source(tmp_path):
    workspace, manifest_path = _workspace(tmp_path, prepare=False)
    source = workspace / "tests/v2/test_target.py"
    source.write_text("api_key = 'gsk_12345678901234567890'\n", encoding="utf-8")
    subprocess.run(
        ["git", "-c", f"safe.directory={workspace.as_posix()}", "add", "-A"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-c", f"safe.directory={workspace.as_posix()}", "-c", "user.email=worker-tests@example.invalid", "-c", "user.name=Worker Tests", "commit", "-m", "secret fixture"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["base_revision"] = subprocess.run(
        ["git", "-c", f"safe.directory={workspace.as_posix()}", "rev-parse", "HEAD"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(DevFarmError, match="secret"):
        _input_context(workspace, manifest)


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
