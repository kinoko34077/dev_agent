from __future__ import annotations

import json
from pathlib import Path

from scripts.devfarm import write_result
import scripts.devfarm_artifacts as artifacts_module
from scripts.devfarm_artifacts import list_verification_records, read_latest_result_projection
from scripts.devfarm_repository import read_json
from tests.v2.devfarm_test_support import _workspace


def test_verification_artifacts_use_shared_repository_json_reader(tmp_path: Path, monkeypatch):
    verification_dir = tmp_path / ".devfarm" / "results" / "task-1" / "attempts" / "attempt-1" / "verification"
    verification_dir.mkdir(parents=True)
    path = verification_dir / "record.json"
    path.write_text(json.dumps({"verification_id": "record-1", "verified_at": "now"}), encoding="utf-8")
    calls = []

    def _read(path_value):
        calls.append(path_value)
        return read_json(path_value)

    monkeypatch.setattr(artifacts_module, "read_json", _read)

    assert list_verification_records(tmp_path, "task-1", "attempt-1") == [
        {"verification_id": "record-1", "verified_at": "now"}
    ]
    assert calls == [path]


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
