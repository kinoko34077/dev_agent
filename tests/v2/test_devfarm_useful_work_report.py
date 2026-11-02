from __future__ import annotations

import json
from pathlib import Path

from scripts.report_devfarm_useful_work import main


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _result(*, attempt_id: str, base_revision: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "completed",
        "base_revision": base_revision,
        "changed_files": ["docs/example.md"],
        "worker_metrics": {
            "provider_id": "ollama",
            "provider_binding_id": "ollama:local:gemma4-12b",
            "model_id": "gemma4:12b",
            "host_verified": True,
            "host_tests_passed": True,
            "host_verified_test_count": 1,
            "result_accepted": True,
            "independent_verification": True,
            "durable_recorded": True,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost_minor": 0},
            "elapsed_ms": 100,
        },
        "attempt_id": attempt_id,
    }


def test_report_preserves_historical_host_success_but_fails_closed_for_w6(tmp_path: Path) -> None:
    revision_a = "a" * 40
    revision_b = "b" * 40
    task_a = "historical-task-a"
    task_b = "historical-task-b"
    attempt_a = "attempt-a"
    attempt_b = "attempt-b"
    _write(
        tmp_path / ".devfarm/results" / task_a / "attempts" / attempt_a / "result.json",
        _result(attempt_id=attempt_a, base_revision=revision_a),
    )
    _write(
        tmp_path / ".devfarm/results" / task_b / "result.json",
        _result(attempt_id=attempt_b, base_revision=revision_b),
    )
    selection = tmp_path / "selection.json"
    output = tmp_path / "report.json"
    _write(
        selection,
        {
            "schema_version": 1,
            "report_id": "w6-history",
            "evidence_class": "historical_replay",
            "observations": [
                {
                    "task_id": task_a,
                    "attempt_id": attempt_a,
                    "result_ref": f".devfarm/results/{task_a}/attempts/{attempt_a}/result.json",
                    "task_class": "small_new_file_or_docs",
                    "source_revision": revision_a,
                },
                {
                    "task_id": task_b,
                    "attempt_id": attempt_b,
                    "result_ref": f".devfarm/results/{task_b}/result.json",
                    "task_class": "localized_existing_source",
                    "source_revision": revision_b,
                },
            ],
        },
    )

    assert main([str(selection), "--root", str(tmp_path), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "NOT_VERIFIED"
    assert report["summary"]["host_verified"] == 2
    assert report["summary"]["distinct_task_units"] == 2
    assert report["summary"]["distinct_source_revisions"] == 2
    assert "matched_baseline_missing" in report["missing_acceptance_conditions"]
    assert "integration_evidence_missing" in report["missing_acceptance_conditions"]
    assert "fresh_runtime_acceptance_missing" in report["missing_acceptance_conditions"]


def test_report_rejects_result_reference_escape_and_does_not_write_output(tmp_path: Path) -> None:
    selection = tmp_path / "selection.json"
    output = tmp_path / "report.json"
    _write(
        selection,
        {
            "schema_version": 1,
            "report_id": "w6-invalid",
            "evidence_class": "historical_replay",
            "observations": [
                {
                    "task_id": "task-a",
                    "attempt_id": "attempt-a",
                    "result_ref": "..\\secret.json",
                    "task_class": "small_new_file_or_docs",
                    "source_revision": "a" * 40,
                }
            ],
        },
    )

    assert main([str(selection), "--root", str(tmp_path), "--output", str(output)]) == 2
    assert not output.exists()
