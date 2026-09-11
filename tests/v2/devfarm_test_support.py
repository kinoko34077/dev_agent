import json
import subprocess

from scripts.devfarm import prepare_worktree, write_manifest
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.base import ModelProvider


class _WorkerProvider(ModelProvider):
    provider_id = "cloudflare"
    provider_binding_id = "cloudflare"
    model_id = "@cf/meta/llama-3.1-8b-instruct"
    intelligence_tier = "L1"

    def __init__(self, output: dict):
        self.output = output
        self.request_count = 0

    def request(self, request: ModelRequest) -> ModelResponse:
        self.request_count += 1
        return ModelResponse(
            provider=self.provider_id,
            model=self.model_id,
            text_segments=[json.dumps(self.output)],
        )


class _RawWorkerProvider(ModelProvider):
    provider_id = "cloudflare"
    provider_binding_id = "cloudflare"
    model_id = "@cf/meta/llama-3.1-8b-instruct"
    intelligence_tier = "L1"

    def __init__(self, text: str):
        self.text = text
        self.request_count = 0

    def request(self, request: ModelRequest) -> ModelResponse:
        self.request_count += 1
        return ModelResponse(provider=self.provider_id, model=self.model_id, text_segments=[self.text])


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
            "test_commands": ["python -m pytest tests/v2/test_target.py tests/v2/test_baseline.py -q"],
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
    baseline = root / "tests/v2/test_baseline.py"
    target.parent.mkdir(parents=True)
    target.write_text("def test_target():\n    assert True\n", encoding="utf-8")
    baseline.write_text("def test_baseline():\n    assert True\n", encoding="utf-8")
    _git("add", "tests/v2/test_target.py", "tests/v2/test_baseline.py", cwd=root)
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
        "@@ -1,2 +1,3 @@\n"
        " def test_target():\n"
        "     assert True\n"
        "+    return None\n"
    )
