from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from scripts.compare_devfarm_baseline import main as compare_main
from scripts.devfarm_baseline import summarize_observations
from scripts.report_devfarm_baseline import main


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _manifest(task_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_id": task_id,
        "task_type": "worker",
        "objective": "report a bounded result",
        "base_revision": "a" * 40,
        "allowed_files": ["src/example.py"],
        "read_files": ["src/example.py"],
        "forbidden_files": [],
        "external_provider_allowed": True,
        "approved_provider_ids": ["ollama"],
        "outbound_files": ["src/example.py"],
        "new_files": [],
        "requirements": ["preserve the contract"],
        "acceptance": ["the focused test passes"],
        "test_commands": ["python -m compileall -q src/example.py"],
        "output_contract": {"mode": "minimal_file_replacement"},
    }


def _result(attempt_id: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "failed",
        "attempt_id": attempt_id,
        "worker_metrics": {
            "provider_id": "ollama",
            "provider_binding_id": "ollama:local:qwen3.5-9b",
            "model_id": "qwen3.5:9b",
            "proposal_preflight": {"status": "rejected"},
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "cost_minor": 0},
        },
        "known_issues": ["invalid JSON"],
        "failure_spec": {
            "failure_class": "FORMAT_PATCH",
            "stage": "worker_output_validation",
        },
    }


def test_cli_writes_bounded_report_from_explicit_selection(tmp_path: Path, capsys) -> None:
    task_id = "report-cli-task"
    attempt_id = "report-cli-attempt"
    _write_json(tmp_path / ".devfarm/tasks" / f"{task_id}.json", _manifest(task_id))
    _write_json(
        tmp_path / ".devfarm/results" / task_id / "attempts" / attempt_id / "result.json",
        _result(attempt_id),
    )
    selection = tmp_path / "selection.json"
    output = tmp_path / "report.json"
    _write_json(
        selection,
        {
            "schema_version": 1,
            "report_id": "w0-cli",
            "baseline_id": "w0-observed",
            "source_revision": "a" * 40,
            "observations": [
                {
                    "task_id": task_id,
                    "attempt_id": attempt_id,
                    "task_class": "schema_contract_violation",
                    "evidence_class": "fresh_runtime",
                }
            ],
        },
    )

    assert main([str(selection), "--root", str(tmp_path), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["evidence_status"] == "observed_attempts"
    assert report["summary"]["pre_host_failures"] == 1
    assert capsys.readouterr().out == ""


def test_cli_fails_without_mutating_output_on_invalid_selection(tmp_path: Path, capsys) -> None:
    selection = tmp_path / "selection.json"
    output = tmp_path / "report.json"
    _write_json(selection, {"schema_version": 1, "unexpected": True})

    assert main([str(selection), "--root", str(tmp_path), "--output", str(output)]) == 2
    assert not output.exists()
    assert "unknown field" in capsys.readouterr().err


def test_cli_script_entrypoint_works_when_invoked_by_path(tmp_path: Path) -> None:
    task_id = "report-entrypoint-task"
    attempt_id = "report-entrypoint-attempt"
    _write_json(tmp_path / ".devfarm/tasks" / f"{task_id}.json", _manifest(task_id))
    _write_json(
        tmp_path / ".devfarm/results" / task_id / "attempts" / attempt_id / "result.json",
        _result(attempt_id),
    )
    selection = tmp_path / "selection.json"
    output = tmp_path / "report.json"
    _write_json(
        selection,
        {
            "schema_version": 1,
            "report_id": "w0-entrypoint",
            "baseline_id": "w0-observed",
            "source_revision": "a" * 40,
            "observations": [
                {
                    "task_id": task_id,
                    "attempt_id": attempt_id,
                    "task_class": "schema_contract_violation",
                    "evidence_class": "fresh_runtime",
                }
            ],
        },
    )
    script = Path(__file__).parents[2] / "scripts" / "report_devfarm_baseline.py"
    result = subprocess.run(
        [sys.executable, str(script), str(selection), "--root", str(tmp_path), "--output", str(output)],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["observation_count"] == 1


def test_compare_cli_writes_only_a_paired_observed_comparison(tmp_path: Path) -> None:
    def row(task_id: str, *, accepted: bool, parsed: bool) -> dict[str, object]:
        return {
            "task_id": task_id,
            "attempt_id": f"attempt-{task_id}",
            "task_fingerprint": f"fingerprint-{task_id}",
            "task_class": "localized_existing_source",
            "evidence_class": "fresh_runtime",
            "source_revision": "a" * 40,
            "proposal_parse": "accepted" if parsed else "rejected",
            "host_verification": "passed" if accepted else "not_observed",
            "reviewed": accepted,
            "integrated": accepted,
            "accepted": accepted,
            "attempt_count": 1,
            "retry_count": 0,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cost_minor": 0,
            "elapsed_ms": 10,
            "host_execution_ms": 5 if accepted else None,
            "human_intervention_count": 0 if accepted else None,
            "verification_count": 1 if accepted else 0,
            "feasibility": None,
        }

    baseline_rows = [row("pair-a", accepted=False, parsed=False), row("pair-b", accepted=True, parsed=True)]
    treatment_rows = [row("pair-a", accepted=True, parsed=True), row("pair-b", accepted=True, parsed=True)]

    def report(report_id: str, rows: list[dict[str, object]]) -> dict[str, object]:
        return {
            "schema_version": 1,
            "evidence_status": "observed_attempts",
            "baseline_id": report_id,
            "source_revision": "a" * 40,
            "observations": rows,
            "summary": summarize_observations(
                baseline_id=report_id,
                source_revision="a" * 40,
                observations=rows,
            ),
        }

    baseline = tmp_path / "baseline.json"
    treatment = tmp_path / "treatment.json"
    output = tmp_path / "comparison.json"
    _write_json(baseline, report("baseline", baseline_rows))
    _write_json(treatment, report("treatment", treatment_rows))

    assert compare_main(
        [str(baseline), str(treatment), "--root", str(tmp_path), "--output", str(output)]
    ) == 0
    comparison = json.loads(output.read_text(encoding="utf-8"))
    assert comparison["status"] == "PAIRED"
    assert comparison["baseline"]["pre_host_failures"] == 1
    assert comparison["conclusion"] == "improved"
