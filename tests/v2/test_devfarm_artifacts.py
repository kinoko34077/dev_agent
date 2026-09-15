from __future__ import annotations

import json
from pathlib import Path

from scripts.devfarm import write_result
from scripts.devfarm_artifacts import read_latest_result_projection
from tests.v2.devfarm_test_support import _workspace


def test_read_latest_result_projection_validates_the_shared_result_boundary(tmp_path: Path) -> None:
    root, manifest_path = _workspace(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    write_result(
        root,
        {
            "status": "completed",
            "base_revision": manifest["base_revision"],
            "changed_files": [],
            "tests_run": [],
            "tests_passed": True,
            "model_claims": {},
            "proposed_test_commands": [],
            "host_verified_tests": [],
            "worker_metrics": {},
            "known_issues": [],
            "assumptions": [],
            "attempt_id": "attempt-1",
        },
        manifest=manifest,
    )

    result = read_latest_result_projection(root, manifest)

    assert result["status"] == "completed"
    assert result["attempt_id"] == "attempt-1"
