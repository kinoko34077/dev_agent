from __future__ import annotations

import json

import pytest

from scripts.devfarm_worker import apply_and_verify, run_worker
from tests.v2.devfarm_test_support import _WorkerProvider, _workspace

pytestmark = pytest.mark.synthetic_no_charge_worker_billing


def test_new_file_replacement_is_applied_and_verified_by_host(tmp_path) -> None:
    root, manifest_path = _workspace(tmp_path, prepare=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "allowed_files": [
                "tests/v2/test_target.py",
                "tests/v2/generated.py",
            ],
            "read_files": ["tests/v2/test_target.py"],
            "outbound_files": ["tests/v2/test_target.py"],
            "new_files": ["tests/v2/generated.py"],
            "test_commands": [
                "python -m pytest tests/v2/test_target.py tests/v2/generated.py -q"
            ],
            "output_contract": {
                "format": "json",
                "mode": "minimal_file_replacement",
            },
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    proposed = run_worker(
        root,
        manifest_path,
        provider=_WorkerProvider(
            {
                "file_replacements": {
                    "tests/v2/generated.py": [
                        "def test_generated_file_is_executable():",
                        "    assert True",
                    ]
                }
            }
        ),
    )

    assert proposed["status"] == "completed"
    assert proposed["changed_files"] == ["tests/v2/generated.py"]
    assert proposed["worker_metrics"]["host_generated_patch"] == "file_replacements"

    verified = apply_and_verify(
        root,
        manifest_path,
        trust_level="TRUSTED_HOST_EXEC",
        operator_approved=True,
    )

    assert verified["status"] == "completed"
    assert verified["tests_passed"] is True
    assert verified["worker_metrics"]["result_accepted"] is True
    worktree = root / ".devfarm" / "worktrees" / "worker-test-001"
    assert (worktree / "tests/v2/generated.py").is_file()
