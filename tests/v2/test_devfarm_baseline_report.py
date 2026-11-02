from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

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
