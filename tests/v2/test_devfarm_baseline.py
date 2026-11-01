from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.devfarm_baseline import (
    BaselineError,
    compare_observation_cohorts,
    collect_attempt_observation,
    load_fixture_set,
    summarize_observations,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fixture_set() -> dict[str, object]:
    return {
        "schema_version": 1,
        "baseline_id": "weak-model-compensation-w0",
        "source_revision": "a" * 40,
        "fixtures": [
            {
                "fixture_id": "localized-source",
                "task_class": "localized_existing_source",
                "objective": "change one existing implementation branch",
                "allowed_files": ["src/example.py"],
                "read_files": ["src/example.py"],
                "outbound_files": ["src/example.py"],
                "new_files": [],
                "acceptance": ["the focused test passes"],
                "test_commands": ["python -m pytest tests/v2/test_example.py -q"],
                "approved_provider_ids": ["ollama"],
                "output_modes": ["minimal_file_replacement"],
            },
            {
                "fixture_id": "new-doc",
                "task_class": "small_new_file_or_docs",
                "objective": "add one small documentation file",
                "allowed_files": ["docs/example.md"],
                "read_files": [],
                "outbound_files": [],
                "new_files": ["docs/example.md"],
                "acceptance": ["the file exists"],
                "test_commands": ["python -m compileall -q src"],
                "approved_provider_ids": ["ollama"],
                "output_modes": ["minimal_file_replacement"],
            },
            {
                "fixture_id": "syntax-repair",
                "task_class": "syntax_import_repair",
                "objective": "repair one bounded import or syntax defect",
                "allowed_files": ["src/example.py"],
                "read_files": ["src/example.py"],
                "outbound_files": ["src/example.py"],
                "new_files": [],
                "acceptance": ["the module imports"],
                "test_commands": ["python -m compileall -q src/example.py"],
                "approved_provider_ids": ["ollama"],
                "output_modes": ["minimal_file_replacement"],
            },
            {
                "fixture_id": "schema-failure",
                "task_class": "schema_contract_violation",
                "objective": "return a contract-compliant bounded proposal",
                "allowed_files": ["src/example.py"],
                "read_files": ["src/example.py"],
                "outbound_files": ["src/example.py"],
                "new_files": [],
                "acceptance": ["the response matches the output schema"],
                "test_commands": ["python -m compileall -q src/example.py"],
                "approved_provider_ids": ["ollama"],
                "output_modes": ["minimal_file_replacement"],
            },
        ],
    }


def _manifest(task_id: str, *, base_revision: str = "a" * 40) -> dict[str, object]:
    return {
        "schema_version": 1,
        "task_id": task_id,
        "task_type": "worker",
        "objective": "bounded worker task",
        "base_revision": base_revision,
        "allowed_files": ["src/example.py"],
        "read_files": ["src/example.py"],
        "forbidden_files": [],
        "external_provider_allowed": True,
        "approved_provider_ids": ["ollama"],
        "outbound_files": ["src/example.py"],
        "new_files": [],
        "requirements": ["preserve the public contract"],
        "acceptance": ["the focused test passes"],
        "test_commands": ["python -m compileall -q src/example.py"],
        "max_attempts": 1,
        "output_contract": {"mode": "minimal_file_replacement"},
    }


def _result(*, attempt_id: str, accepted: bool, parsed: bool = True) -> dict[str, object]:
    metrics = {
        "provider_id": "ollama",
        "provider_binding_id": "ollama:local:gemma4-12b",
        "model_id": "gemma4:12b",
        "task_type": "worker",
        "elapsed_ms": 1200,
        "attempt_count": 1,
        "retry_count": 0,
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "cost_minor": 0},
        "proposal_preflight": {"status": "accepted" if parsed else "rejected"},
        "host_verified": accepted,
        "host_tests_passed": accepted,
        "result_accepted": accepted,
    }
    value: dict[str, object] = {
        "schema_version": 1,
        "status": "completed" if parsed else "failed",
        "attempt_id": attempt_id,
        "base_revision": "a" * 40,
        "changed_files": ["src/example.py"] if parsed else [],
        "tests_run": [],
        "tests_passed": accepted,
        "model_claims": {},
        "proposed_test_commands": [],
        "host_verified_tests": (
            [{"command": "python -m compileall -q src/example.py", "exit_code": 0, "passed": True}]
            if accepted
            else []
        ),
        "worker_metrics": metrics,
        "known_issues": [] if parsed else ["worker response JSON is invalid"],
        "assumptions": [],
    }
    if not parsed:
        value["failure_spec"] = {
            "failure_class": "FORMAT_PATCH",
            "stage": "worker_output_validation",
            "location": "worker_output",
            "observed": "invalid JSON",
            "expected": "one JSON object",
            "problem": "the response could not be decoded",
            "required_correction": "return one JSON object",
            "must_preserve": [],
            "forbidden_changes": [],
            "acceptance_checks": ["response parses"],
            "validator_refs": ["worker:output_contract"],
        }
    return value


def test_fixture_set_is_reusable_and_rejects_mixed_revision() -> None:
    fixtures = load_fixture_set(_fixture_set())

    assert fixtures.source_revision == "a" * 40
    assert {item.task_class for item in fixtures.fixtures} == {
        "localized_existing_source",
        "small_new_file_or_docs",
        "syntax_import_repair",
        "schema_contract_violation",
    }

    invalid = _fixture_set()
    invalid["fixtures"] = [dict(fixtures.fixtures[0].to_dict(), source_revision="b" * 40)]
    with pytest.raises(BaselineError, match="source_revision"):
        load_fixture_set(invalid)


def test_repository_w0_fixture_definition_is_pinned_and_complete() -> None:
    path = Path("spec/v2/evidence/weak-model-compensation-w0-fixtures.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    fixtures = load_fixture_set(payload)

    assert payload["evidence_status"] == "synthetic_fixture_only"
    assert fixtures.source_revision == payload["implementation_baseline"]
    assert {item.task_class for item in fixtures.fixtures} == {
        "localized_existing_source",
        "small_new_file_or_docs",
        "syntax_import_repair",
        "schema_contract_violation",
    }
    assert all(item.approved_provider_ids == ("ollama",) for item in fixtures.fixtures)


def test_collect_attempt_includes_pre_host_failure_and_explicit_unknowns(tmp_path: Path) -> None:
    task_id = "baseline-task"
    attempt_id = "attempt-1"
    manifest = _manifest(task_id)
    _write_json(tmp_path / ".devfarm/tasks" / f"{task_id}.json", manifest)
    _write_json(
        tmp_path / ".devfarm/results" / task_id / "attempts" / attempt_id / "result.json",
        _result(attempt_id=attempt_id, accepted=False, parsed=False),
    )

    observation = collect_attempt_observation(
        tmp_path,
        task_id=task_id,
        attempt_id=attempt_id,
        task_class="schema_contract_violation",
        evidence_class="historical_replay",
    )

    assert observation["proposal_parse"] == "rejected"
    assert observation["host_verification"] == "not_observed"
    assert observation["accepted"] is False
    assert observation["failure_class"] == "FORMAT_PATCH"
    assert observation["repair_stage"] == "initial"
    assert observation["evidence_class"] == "historical_replay"
    assert observation["host_execution_ms"] is None
    assert observation["human_intervention_count"] is None


def test_summary_counts_attempts_before_host_and_keeps_resource_totals(tmp_path: Path) -> None:
    task_id = "baseline-task"
    attempt_id = "attempt-1"
    _write_json(tmp_path / ".devfarm/tasks" / f"{task_id}.json", _manifest(task_id))
    _write_json(
        tmp_path / ".devfarm/results" / task_id / "attempts" / attempt_id / "result.json",
        _result(attempt_id=attempt_id, accepted=True),
    )
    _write_json(
        tmp_path / ".devfarm/results" / task_id / "attempts" / attempt_id / "verification" / "v1.json",
        {
            "verification_id": "v1",
            "independent_verification": True,
            "verified_tests": [{"passed": True}],
        },
    )
    observation = collect_attempt_observation(
        tmp_path,
        task_id=task_id,
        attempt_id=attempt_id,
        task_class="localized_existing_source",
        evidence_class="fresh_runtime",
    )

    summary = summarize_observations(
        baseline_id="w0",
        source_revision="a" * 40,
        observations=[observation],
    )

    assert summary["attempts_total"] == 1
    assert summary["proposal_parsed"] == 1
    assert summary["host_verified"] == 1
    assert summary["accepted"] == 1
    assert summary["totals"] == {"prompt_tokens": 10, "completion_tokens": 20, "cost_minor": 0, "elapsed_ms": 1200}
    assert summary["by_evidence_class"] == {"fresh_runtime": 1}
    assert summary["by_repair_stage"] == {"initial": 1}


def test_collect_attempt_labels_fallback_without_treating_it_as_initial(tmp_path: Path) -> None:
    task_id = "baseline-fallback"
    attempt_id = "attempt-fallback"
    manifest = _manifest(task_id)
    manifest["rework_handoff"] = {
        "repair_context": {
            "fallback_from_provider": "ollama:local:qwen3.5-4b",
            "fallback_to_provider": "ollama:local:gemma4-12b",
        }
    }
    _write_json(tmp_path / ".devfarm/tasks" / f"{task_id}.json", manifest)
    _write_json(
        tmp_path / ".devfarm/results" / task_id / "attempts" / attempt_id / "result.json",
        _result(attempt_id=attempt_id, accepted=False),
    )

    observation = collect_attempt_observation(
        tmp_path,
        task_id=task_id,
        attempt_id=attempt_id,
        task_class="schema_contract_violation",
        evidence_class="historical_replay",
    )

    assert observation["repair_stage"] == "format_fallback"


def _cohort_observation(
    task_id: str,
    *,
    fingerprint: str | None = None,
    accepted: bool,
    parsed: bool,
    cost_minor: int = 0,
    elapsed_ms: int = 100,
) -> dict[str, object]:
    return {
        "task_id": task_id,
        "task_fingerprint": fingerprint or f"fingerprint-{task_id}",
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
        "prompt_tokens": 100,
        "completion_tokens": 30,
        "cost_minor": cost_minor,
        "elapsed_ms": elapsed_ms,
        "host_execution_ms": 20 if accepted else None,
        "human_intervention_count": 0 if accepted else None,
    }


def test_compare_observation_cohorts_keeps_pre_host_failures_in_the_denominator() -> None:
    baseline = [
        _cohort_observation("task-1", accepted=False, parsed=False, cost_minor=0, elapsed_ms=80),
        _cohort_observation("task-2", accepted=True, parsed=True, cost_minor=2, elapsed_ms=120),
    ]
    treatment = [
        _cohort_observation("task-1", accepted=True, parsed=True, cost_minor=1, elapsed_ms=90),
        _cohort_observation("task-2", accepted=True, parsed=True, cost_minor=1, elapsed_ms=110),
    ]

    result = compare_observation_cohorts(
        baseline_id="full-replacement",
        treatment_id="localized-edit",
        baseline=baseline,
        treatment=treatment,
    )

    assert result["status"] == "PAIRED"
    assert result["matched_tasks"] == 2
    assert result["baseline"]["attempts"] == 2
    assert result["baseline"]["accepted"] == 1
    assert result["baseline"]["proposal_parsed"] == 1
    assert result["treatment"]["accepted"] == 2
    assert result["treatment"]["proposal_parsed"] == 2
    assert result["delta"]["accepted"] == 1
    assert result["conclusion"] == "improved"


def test_compare_observation_cohorts_refuses_unmatched_claims() -> None:
    baseline = [_cohort_observation("task-1", accepted=False, parsed=False)]
    treatment = [_cohort_observation("task-2", fingerprint="fingerprint-2", accepted=True, parsed=True)]

    result = compare_observation_cohorts(
        baseline_id="baseline",
        treatment_id="treatment",
        baseline=baseline,
        treatment=treatment,
    )

    assert result["status"] == "INSUFFICIENT_MATCHED_DATA"
    assert result["matched_tasks"] == 0
    assert result["conclusion"] == "insufficient_evidence"
    assert result["unmatched_baseline"] == ["task-1"]
    assert result["unmatched_treatment"] == ["task-2"]


def test_compare_observation_cohorts_rejects_duplicate_task_fingerprints() -> None:
    duplicate = _cohort_observation("task-1", accepted=False, parsed=False)
    with pytest.raises(BaselineError, match="duplicate"):
        compare_observation_cohorts(
            baseline_id="baseline",
            treatment_id="treatment",
            baseline=[duplicate, dict(duplicate, task_id="task-2")],
            treatment=[_cohort_observation("task-1", accepted=True, parsed=True)],
        )
