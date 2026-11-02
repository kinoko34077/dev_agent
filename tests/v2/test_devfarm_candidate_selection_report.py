from __future__ import annotations

import json
from pathlib import Path

from scripts.devfarm_candidate_selection_report import main


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _candidate(candidate_id: str, attempt_id: str) -> dict[str, object]:
    return {
        "candidate_id": candidate_id,
        "attempt_id": attempt_id,
        "artifact_ref": f".devfarm/results/{candidate_id}/result.json",
        "scope_key": "tests/v2/test_devfarm_candidate_selection.py:region-1",
        "provider_binding_id": "ollama:local:qwen3.5-9b",
        "model_id": "qwen3.5:9b",
        "prompt_variant": candidate_id,
        "context_variant": "base",
        "resource_units": 1,
        "stages": [
            {
                "stage": "parser_schema",
                "status": "fail",
                "evidence_ref": f".devfarm/results/{candidate_id}/result.json",
                "reason_code": "WORKER_OUTPUT_INVALID_JSON",
            },
            {"stage": "patch_applicability", "status": "not_run"},
        ],
    }


def test_candidate_report_reads_explicit_attempts_and_preserves_correlated_failure(tmp_path: Path) -> None:
    for candidate_id in ("candidate-a", "candidate-b"):
        _write_json(
            tmp_path / ".devfarm/results" / candidate_id / "result.json",
            {"attempt_id": f"attempt-{candidate_id}", "status": "failed"},
        )
    selection = {
        "schema_version": 1,
        "report_id": "w4-report",
        "logical_task_id": "issue124-guard-fail-closed-tests-qwen-20261102",
        "source_revision": "4bd2a2b683413f4d192389cc6664fc89145e5457",
        "evidence_class": "fresh_runtime",
        "max_candidates": 2,
        "max_resource_units": 2,
        "candidates": [
            _candidate("candidate-a", "attempt-candidate-a"),
            _candidate("candidate-b", "attempt-candidate-b"),
        ],
    }
    selection_path = tmp_path / "selection.json"
    output_path = tmp_path / "report.json"
    _write_json(selection_path, selection)

    assert main([str(selection_path), "--root", str(tmp_path), "--output", str(output_path)]) == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))

    assert report["evidence_status"] == "observed_candidate_selection"
    assert report["selection"]["accepted_candidate_id"] is None
    assert report["selection"]["considered_candidate_ids"] == ["candidate-a", "candidate-b"]
    assert report["selection"]["correlated_failure_signatures"] == [
        "parser_schema:WORKER_OUTPUT_INVALID_JSON"
    ]
    assert report["selection"]["resource_units_used"] == 2


def test_candidate_report_rejects_artifact_outside_results_boundary(tmp_path: Path) -> None:
    selection = {
        "schema_version": 1,
        "report_id": "w4-unsafe",
        "logical_task_id": "logical-task",
        "source_revision": "a" * 40,
        "evidence_class": "fresh_runtime",
        "candidates": [
            {
                **_candidate("candidate-a", "attempt-a"),
                "artifact_ref": "docs/CURRENT_STATE.md",
            }
        ],
    }
    selection_path = tmp_path / "selection.json"
    _write_json(selection_path, selection)

    assert main([str(selection_path), "--root", str(tmp_path)]) == 2
