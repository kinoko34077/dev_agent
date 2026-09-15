from __future__ import annotations

from pathlib import Path

from scripts.devfarm_manifests import load_worker_manifest
from tests.v2.devfarm_test_support import _workspace


def test_load_worker_manifest_checks_plan_task_identity_and_ownership(tmp_path: Path) -> None:
    root, manifest_path = _workspace(tmp_path)
    task = {
        "task_id": "worker-test-001",
        "manifest_path": manifest_path.relative_to(root).as_posix(),
        "ownership": ["tests/v2/test_target.py"],
    }

    loaded_path, manifest = load_worker_manifest(root, task)

    assert loaded_path == manifest_path
    assert manifest["task_id"] == task["task_id"]
