from __future__ import annotations

import pytest
from dataclasses import replace

from scripts.devfarm_candidate_selection import (
    CandidateSelectionError,
    CandidateStage,
    WorkerProposalCandidate,
    select_staged_candidates,
)


STAGES = (
    "parser_schema",
    "patch_applicability",
    "deterministic_tests",
    "host_verification",
    "review_integration",
)


def _candidate(
    candidate_id: str,
    *,
    attempt_id: str | None = None,
    provider_binding_id: str = "ollama-local",
    model_id: str = "qwen",
    prompt_variant: str = "minimal",
    context_variant: str = "base",
    statuses: dict[str, str] | None = None,
    reason_code: str | None = None,
    resource_units: int = 1,
    artifact_ref: str | None = None,
) -> WorkerProposalCandidate:
    values = statuses or {stage: "pass" for stage in STAGES}
    return WorkerProposalCandidate(
        logical_task_id="task-1",
        candidate_id=candidate_id,
        attempt_id=attempt_id or f"attempt-{candidate_id}",
        artifact_ref=artifact_ref or f"artifacts/{candidate_id}.json",
        source_revision="a" * 40,
        scope_key="src/example.py:region-1",
        provider_binding_id=provider_binding_id,
        model_id=model_id,
        prompt_variant=prompt_variant,
        context_variant=context_variant,
        stages=tuple(
            CandidateStage(
                stage=stage,
                status=values.get(stage, "not_run"),
                reason_code=reason_code if values.get(stage) == "fail" else None,
            )
            for stage in STAGES
        ),
        resource_units=resource_units,
    )


def test_first_fully_verified_candidate_is_selected_and_later_candidates_are_not_consumed() -> None:
    result = select_staged_candidates(
        [
            _candidate(
                "candidate-b",
                provider_binding_id="ollama-fallback",
                model_id="gemma",
                prompt_variant="localized",
            ),
            _candidate("candidate-a"),
        ],
        logical_task_id="task-1",
        max_candidates=4,
        max_resource_units=4,
    )

    assert result.accepted_candidate_id == "candidate-a"
    assert result.considered_candidate_ids == ("candidate-a",)
    assert result.stop_reason == "accepted"
    assert result.resource_units_used == 1
    assert result.stage_counts["review_integration"] == 1


def test_failed_parser_candidate_is_rejected_before_later_stages() -> None:
    result = select_staged_candidates(
        [
            _candidate(
                "candidate-a",
                statuses={"parser_schema": "fail"},
                reason_code="WORKER_OUTPUT_INVALID_JSON",
            ),
            _candidate("candidate-b", provider_binding_id="ollama-fallback", model_id="gemma"),
        ],
        logical_task_id="task-1",
        max_candidates=2,
        max_resource_units=2,
    )

    assert result.accepted_candidate_id == "candidate-b"
    assert result.rejected_candidate_ids == ("candidate-a",)
    assert result.failure_signatures == ("parser_schema:WORKER_OUTPUT_INVALID_JSON",)
    assert result.stage_counts["patch_applicability"] == 1


def test_correlated_failures_are_reported_without_treating_candidate_count_as_success() -> None:
    candidates = [
        _candidate(
            "candidate-a",
            statuses={"parser_schema": "fail"},
            reason_code="WORKER_OUTPUT_INVALID_JSON",
        ),
        _candidate(
            "candidate-b",
            provider_binding_id="ollama-fallback",
            model_id="gemma",
            prompt_variant="localized",
            statuses={"parser_schema": "fail"},
            reason_code="WORKER_OUTPUT_INVALID_JSON",
        ),
    ]
    result = select_staged_candidates(
        candidates,
        logical_task_id="task-1",
        max_candidates=2,
        max_resource_units=2,
    )

    assert result.accepted_candidate_id is None
    assert result.stop_reason == "candidates_exhausted"
    assert result.failure_signatures == (
        "parser_schema:WORKER_OUTPUT_INVALID_JSON",
        "parser_schema:WORKER_OUTPUT_INVALID_JSON",
    )
    assert result.correlated_failure_signatures == ("parser_schema:WORKER_OUTPUT_INVALID_JSON",)


def test_resource_cap_stops_before_unbounded_candidate_generation() -> None:
    result = select_staged_candidates(
        [_candidate("candidate-a", resource_units=2), _candidate("candidate-b", resource_units=2)],
        logical_task_id="task-1",
        max_candidates=4,
        max_resource_units=1,
    )

    assert result.accepted_candidate_id is None
    assert result.considered_candidate_ids == ()
    assert result.stop_reason == "resource_cap"
    assert result.resource_units_used == 0


@pytest.mark.parametrize("field", ["candidate_id", "attempt_id", "artifact_ref"])
def test_candidate_identity_must_be_unique(field: str) -> None:
    first = _candidate("candidate-a")
    second = _candidate("candidate-b")
    if field == "candidate_id":
        second = _candidate("candidate-a", attempt_id="attempt-b", artifact_ref="artifacts/b.json")
    elif field == "attempt_id":
        second = _candidate("candidate-b", attempt_id=first.attempt_id)
    else:
        second = _candidate("candidate-b", artifact_ref=first.artifact_ref)

    with pytest.raises(CandidateSelectionError, match="unique"):
        select_staged_candidates([first, second], logical_task_id="task-1")


def test_external_effect_candidate_is_rejected_at_boundary() -> None:
    with pytest.raises(CandidateSelectionError, match="external effect"):
        replace(_candidate("candidate-a"), external_effect_key="publish-op-1")


def test_candidates_must_belong_to_one_logical_task() -> None:
    other = _candidate("candidate-b")
    other = WorkerProposalCandidate(
        logical_task_id="task-2",
        candidate_id=other.candidate_id,
        attempt_id=other.attempt_id,
        artifact_ref=other.artifact_ref,
        source_revision=other.source_revision,
        scope_key=other.scope_key,
        provider_binding_id=other.provider_binding_id,
        model_id=other.model_id,
        prompt_variant=other.prompt_variant,
        context_variant=other.context_variant,
        stages=other.stages,
        resource_units=other.resource_units,
    )
    with pytest.raises(CandidateSelectionError, match="logical_task_id"):
        select_staged_candidates([_candidate("candidate-a"), other], logical_task_id="task-1")
