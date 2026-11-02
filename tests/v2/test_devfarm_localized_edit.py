from __future__ import annotations

import json

import pytest

from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_localized_edit import materialize_localized_edits
from scripts.devfarm_worker import run_worker
from scripts.devfarm_worker_output import (
    localized_worker_output_schema,
    worker_output_mode,
)
from tests.v2.devfarm_test_support import _WorkerProvider, _workspace


pytestmark = pytest.mark.synthetic_no_charge_worker_billing


def _manifest() -> dict[str, object]:
    return {
        "task_id": "localized-edit-task",
        "base_revision": "a" * 40,
        "allowed_files": ["src/example.py"],
        "outbound_files": ["src/example.py"],
        "new_files": [],
    }


def _reader(revision: str, path: str) -> bytes:
    assert revision == "a" * 40
    assert path == "src/example.py"
    return b"def example():\n    return 1\n\n"


def test_materialize_localized_edit_builds_host_owned_patch() -> None:
    result = materialize_localized_edits(
        _manifest(),
        [
            {
                "path": "src/example.py",
                "start_line": 2,
                "end_line": 2,
                "expected_lines": ["    return 1"],
                "replacement_lines": ["    return 2"],
            }
        ],
        read_file_at_revision=_reader,
    )

    assert result.changed_files == ("src/example.py",)
    assert result.edit_count == 1
    assert result.line_ranges == (("src/example.py", 2, 2),)
    assert result.patch.startswith("diff --git a/src/example.py b/src/example.py\n")
    assert "-    return 1\n+    return 2\n" in result.patch


def test_materialize_localized_edit_rejects_stale_anchor() -> None:
    with pytest.raises(DevFarmError, match="expected_lines"):
        materialize_localized_edits(
            _manifest(),
            [
                {
                    "path": "src/example.py",
                    "start_line": 2,
                    "end_line": 2,
                    "expected_lines": ["    return stale"],
                    "replacement_lines": ["    return 2"],
                }
            ],
            read_file_at_revision=_reader,
        )


def test_materialize_localized_edit_rejects_overlap_and_scope_escape() -> None:
    with pytest.raises(DevFarmError, match="overlap"):
        materialize_localized_edits(
            _manifest(),
            [
                {
                    "path": "src/example.py",
                    "start_line": 1,
                    "end_line": 2,
                    "expected_lines": ["def example():", "    return 1"],
                    "replacement_lines": ["def example():", "    return 2"],
                },
                {
                    "path": "src/example.py",
                    "start_line": 2,
                    "end_line": 2,
                    "expected_lines": ["    return 1"],
                    "replacement_lines": ["    return 3"],
                },
            ],
            read_file_at_revision=_reader,
        )

    with pytest.raises(DevFarmError, match="outbound_files"):
        materialize_localized_edits(
            _manifest(),
            [
                {
                    "path": "tests/test_other.py",
                    "start_line": 1,
                    "end_line": 1,
                    "expected_lines": ["old"],
                    "replacement_lines": ["new"],
                }
            ],
            read_file_at_revision=lambda _revision, _path: b"old\n",
        )


def test_localized_output_mode_is_explicit_and_path_bound() -> None:
    assert worker_output_mode({"format": "json"}, local_ollama=True) == "minimal_file_replacement"
    assert worker_output_mode({"mode": "localized_file_edit"}, local_ollama=True) == "localized_file_edit"
    schema = localized_worker_output_schema(allowed_paths=("src/example.py",))
    edit = schema["properties"]["file_edits"]["items"]
    assert edit["properties"]["path"]["enum"] == ["src/example.py"]
    assert edit["additionalProperties"] is False


def test_localized_worker_mode_materializes_host_owned_patch(tmp_path) -> None:
    root, manifest_path = _workspace(tmp_path, prepare=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_contract"] = {
        "format": "json",
        "mode": "localized_file_edit",
        "expected_output_chars": 512,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    provider = _WorkerProvider(
        {
            "file_edits": [
                {
                    "path": "tests/v2/test_target.py",
                    "start_line": 2,
                    "end_line": 2,
                    "expected_lines": ["    assert True"],
                    "replacement_lines": ["    assert False"],
                }
            ]
        }
    )

    result = run_worker(root, manifest_path, provider=provider)

    assert result["status"] == "completed"
    assert result["changed_files"] == ["tests/v2/test_target.py"]
    assert result["worker_metrics"]["host_generated_patch"] == "localized_file_edits"
    assert result["worker_metrics"]["localized_edit_count"] == 1


def test_localized_worker_rejects_embedded_newline_and_emits_repair(tmp_path) -> None:
    root, manifest_path = _workspace(tmp_path, prepare=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_contract"] = {
        "format": "json",
        "mode": "localized_file_edit",
        "expected_output_chars": 512,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    provider = _WorkerProvider(
        {
            "file_edits": [
                {
                    "path": "tests/v2/test_target.py",
                    "start_line": 2,
                    "end_line": 2,
                    "expected_lines": ["    assert True"],
                    "replacement_lines": ["    assert True\n"],
                }
            ]
        }
    )

    result = run_worker(root, manifest_path, provider=provider)

    assert result["status"] == "failed"
    assert result["worker_metrics"]["proposal_preflight"]["error_code"] == "WORKER_OUTPUT_LINE_SHAPE"
    assert result["repair_directive"]["repair_target"] == "localized_edit.replacement_lines"


def test_host_compensation_switches_rework_attempt_to_localized_mode(tmp_path) -> None:
    root, manifest_path = _workspace(tmp_path, prepare=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_contract"] = {"format": "json", "mode": "minimal_file_replacement"}
    manifest["rework_handoff"] = {
        "repair_context": {
            "compensation": {
                "authority": "host_policy",
                "action": "switch_output_mode",
                "reason_code": "format_existing_source_contract",
                "output_mode": "localized_file_edit",
                "expected_output_chars": 512,
                "context_hints": ["exact_line_anchor"],
                "reframe_required": False,
            }
        }
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    provider = _WorkerProvider(
        {
            "file_edits": [
                {
                    "path": "tests/v2/test_target.py",
                    "start_line": 2,
                    "end_line": 2,
                    "expected_lines": ["    assert True"],
                    "replacement_lines": ["    assert False"],
                }
            ]
        }
    )

    result = run_worker(root, manifest_path, provider=provider)

    assert result["status"] == "completed"
    assert result["worker_metrics"]["output_contract_mode"] == "localized_file_edit"
    assert result["worker_metrics"]["compensation"]["authority"] == "host_policy"


def test_localized_compensation_without_change_budget_stops_before_provider(tmp_path) -> None:
    root, manifest_path = _workspace(tmp_path, prepare=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rework_handoff"] = {
        "repair_context": {
            "compensation": {
                "authority": "host_policy",
                "action": "switch_output_mode",
                "reason_code": "format_existing_source_contract",
                "output_mode": "localized_file_edit",
                "context_hints": ["exact_line_anchor"],
                "reframe_required": False,
            }
        }
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    provider = _WorkerProvider({"file_edits": []})

    result = run_worker(root, manifest_path, provider=provider)

    assert result["status"] == "failed"
    assert provider.request_count == 0
    assert result["worker_metrics"]["pre_dispatch_block"] == "feasibility"
    assert "localized_output_budget_missing" in result["worker_metrics"]["feasibility"]["reasons"]
