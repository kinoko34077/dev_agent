from dataclasses import replace
from uuid import uuid4

import pytest

from src.dev_agent.domain.protocol import IntelligenceTier
from src.dev_agent.intelligence.convergence import (
    ConcreteFailureSpec,
    ConvergenceMetadata,
    ConvergenceState,
    ConvergenceStopReason,
    ConvergenceAssessment,
    ConvergenceObservation,
    FailureFingerprint,
    same_failure_signature,
    ValidationLadder,
    ValidationObservation,
    ValidationRung,
    RepairDirective,
    assess_convergence,
)
from src.dev_agent.intelligence.refinement import (
    BoundedRefinementPolicy,
    FailureClass,
    RefinementContext,
)


def _context(**overrides):
    values = {
        "task_id": "task-convergence-1",
        "failure_class": FailureClass.SEMANTIC_TEST,
        "current_tier": IntelligenceTier.L1,
        "allowed_tiers": (IntelligenceTier.L1, IntelligenceTier.L2),
        "attempt": 1,
        "max_attempts": 6,
        "refinement_round": 1,
        "max_refinement_rounds": 4,
        "escalation_count": 0,
        "max_escalations": 1,
        "now_epoch": 100.0,
        "deadline_epoch": 200.0,
        "budget_remaining": 1.0,
        "estimated_cost": 0.1,
        "external_outcome_known": True,
        "correction_available": True,
        "critic_available": True,
        "alternate_binding_id": None,
        "current_reasoning_effort": "minimal",
        "allowed_reasoning_efforts": ("minimal", "low", "medium"),
        "model_change_used": False,
        "reasoning_escalated": False,
    }
    values.update(overrides)
    return RefinementContext(**values)


def _fingerprint(**overrides):
    values = {
        "failure_class": FailureClass.SEMANTIC_TEST,
        "validator_refs": (
            "tests/v2/test_contract.py::test_shape",
            "verification:contract",
        ),
        "response_contract": None,
        "test_ids": ("tests/v2/test_contract.py::test_shape",),
        "patch_category": "semantic_contract",
        "error_code": "EXPECTED_BEHAVIOR_MISMATCH",
    }
    values.update(overrides)
    return FailureFingerprint.from_observation(**values)


def test_failure_fingerprint_is_canonical_and_order_independent():
    first = _fingerprint()
    second = _fingerprint(
        validator_refs=tuple(reversed(first.validator_refs)),
        test_ids=tuple(reversed(first.test_ids)),
    )

    assert first.failure_signature == second.failure_signature
    assert first.to_dict() == second.to_dict()
    assert same_failure_signature(first, second)
    assert len(first.failure_signature) == 64


def test_failure_fingerprint_changes_for_a_bounded_observation_change():
    first = _fingerprint()
    changed = _fingerprint(error_code="MISSING_EXPECTED_FIELD")

    assert first.failure_signature != changed.failure_signature
    assert not same_failure_signature(first, changed)


def test_failure_fingerprint_rejects_free_form_or_secret_shaped_inputs():
    with pytest.raises(ValueError, match="bounded token"):
        _fingerprint(error_code="the model forgot to close the block")
    with pytest.raises(ValueError, match="secret"):
        _fingerprint(error_code="api_key=AIzaSyA123456789")


def test_failure_fingerprint_round_trip_rejects_digest_tampering():
    fingerprint = _fingerprint()
    restored = FailureFingerprint.from_dict(fingerprint.to_dict())

    assert restored == fingerprint
    tampered = {**fingerprint.to_dict(), "failure_signature": "0" * 64}
    with pytest.raises(ValueError, match="failure_signature"):
        FailureFingerprint.from_dict(tampered)


def test_concrete_failure_spec_and_repair_directive_are_bounded_and_round_trip():
    spec = ConcreteFailureSpec(
        failure_class="FORMAT_PATCH",
        stage="worker_output_validation",
        location="known_issues",
        observed="string",
        expected="array<string>",
        problem="known_issues must be a JSON array",
        required_correction="Return known_issues as an array; use [] when empty.",
        must_preserve=("task objective",),
        forbidden_changes=("changing file_replacements path",),
        acceptance_checks=("known_issues is an array",),
        validator_refs=("worker:output_contract",),
    )
    directive = RepairDirective.from_failure_spec(spec)

    assert spec.failure_class == "format_patch"
    assert len(spec.failure_signature) == 64
    assert ConcreteFailureSpec.from_dict(spec.to_dict()) == spec
    assert RepairDirective.from_dict(directive.to_dict()) == directive
    assert directive.required_action == spec.required_correction
    assert len(directive.directive_hash) == 64
    assert RepairDirective.from_dict(directive.to_dict()).directive_hash == directive.directive_hash


def test_repair_directive_hash_rejects_tampering():
    spec = ConcreteFailureSpec(
        failure_class="FORMAT_PATCH",
        stage="worker_output_validation",
        location="known_issues",
        observed="string",
        expected="array<string>",
        problem="known_issues must be a JSON array",
        required_correction="Return known_issues as an array; use [] when empty.",
        acceptance_checks=("known_issues is an array",),
    )
    directive = RepairDirective.from_failure_spec(spec)
    tampered = {**directive.to_dict(), "required_action": "Return an object instead."}
    with pytest.raises(ValueError, match="directive_hash"):
        RepairDirective.from_dict(tampered)


def test_concrete_failure_spec_rejects_secret_and_vague_repair_directive():
    with pytest.raises(ValueError, match="secret"):
        ConcreteFailureSpec(
            failure_class="FORMAT_PATCH",
            stage="worker_output_validation",
            location="known_issues",
            observed="api_key=AIzaSyA123456789",
            expected="array<string>",
            problem="secret-shaped value",
            required_correction="Return an array of strings.",
        )
    with pytest.raises(ValueError, match="actionable"):
        RepairDirective(
            repair_target="known_issues",
            previous_problem="wrong shape",
            required_action="improve",
            completion_condition=("known_issues is an array",),
        )


def test_fast_path_metadata_is_zero_round_and_has_no_failure_or_extra_work():
    metadata = ConvergenceMetadata.fast_path(current_model_identity="gemini:free-3:model-a")

    assert metadata.convergence_state is ConvergenceState.FAST_PATH
    assert metadata.refinement_round == 0
    assert metadata.failure_class is None
    assert metadata.failure_signature is None
    assert metadata.stop_reason is ConvergenceStopReason.FAST_PATH
    assert metadata.to_dict()["correction_actor"] is None


def test_validation_success_projects_fast_path_without_refinement():
    metadata = ConvergenceMetadata.from_validation(
        passed=True,
        refinement_round=0,
        current_model_identity="gemini:free-3:model-a",
        validator_refs=("schema:planner", "tests/v2/test_planner.py::test_shape"),
    )

    assert metadata.convergence_state is ConvergenceState.FAST_PATH
    assert metadata.stop_reason is ConvergenceStopReason.FAST_PATH
    assert metadata.refinement_round == 0
    assert metadata.failure_signature is None


def test_failed_validation_projects_refinement_and_requires_a_fresh_path():
    fingerprint = _fingerprint()
    metadata = ConvergenceMetadata.from_validation(
        passed=False,
        refinement_round=1,
        current_model_identity="gemini:free-3:model-a",
        source_attempt_id="attempt-source",
        failure=fingerprint,
        correction_actor="l1_critic",
    )

    assert metadata.convergence_state is ConvergenceState.REFINEMENT
    assert metadata.failure_signature == fingerprint.failure_signature
    assert metadata.fresh_attempt_id is None


def test_validation_ladder_short_circuits_at_first_failure_and_restarts_at_v0():
    fingerprint = _fingerprint(error_code="MALFORMED_PATCH")
    ladder = ValidationLadder().record(
        ValidationObservation(ValidationRung.V0, passed=True, validator_refs=("json:parse",))
    )
    failed = ladder.record(
        ValidationObservation(
            ValidationRung.V1,
            passed=False,
            validator_refs=("patch:apply",),
            failure=fingerprint,
        )
    )

    assert failed.first_failure is not None
    assert failed.first_failure.rung is ValidationRung.V1
    assert failed.next_rung is ValidationRung.V0
    assert failed.restart_after_correction() == ValidationLadder()
    with pytest.raises(ValueError, match="fresh attempt"):
        failed.record(ValidationObservation(ValidationRung.V2, passed=True))


def test_validation_ladder_requires_an_ordered_prefix_and_round_trips():
    with pytest.raises(ValueError, match="start at V0"):
        ValidationLadder(
            observations=(ValidationObservation(ValidationRung.V1, passed=True),)
        )
    ladder = ValidationLadder(
        observations=(
            ValidationObservation(ValidationRung.V0, passed=True),
            ValidationObservation(ValidationRung.V1, passed=True),
        )
    )
    assert ValidationLadder.from_dict(ladder.to_dict()) == ladder
    assert ladder.next_rung is ValidationRung.V2


def test_refinement_metadata_binds_source_to_a_distinct_fresh_attempt():
    fingerprint = _fingerprint()
    source_attempt = str(uuid4())
    fresh_attempt = str(uuid4())
    metadata = ConvergenceMetadata(
        refinement_round=1,
        source_attempt_id=source_attempt,
        fresh_attempt_id=fresh_attempt,
        failure_class=FailureClass.SEMANTIC_TEST,
        failure_signature=fingerprint.failure_signature,
        correction_actor="l1_critic",
        previous_model_identity="gemini:free-3:model-a",
        current_model_identity="gemini:free-3:model-a",
        validator_refs=fingerprint.validator_refs,
        convergence_state=ConvergenceState.REFINEMENT,
    )

    restored = ConvergenceMetadata.from_dict(metadata.to_dict())

    assert restored == metadata
    assert restored.source_attempt_id != restored.fresh_attempt_id
    with pytest.raises(ValueError, match="distinct"):
        replace(metadata, fresh_attempt_id=source_attempt)


def test_convergence_metadata_rejects_signature_without_failure_and_invalid_stop():
    with pytest.raises(ValueError, match="failure_signature"):
        ConvergenceMetadata(
            refinement_round=0,
            source_attempt_id=None,
            fresh_attempt_id=None,
            failure_class=None,
            failure_signature="a" * 64,
            correction_actor=None,
            previous_model_identity=None,
            current_model_identity="gemini:free-3:model-a",
            validator_refs=(),
            convergence_state=ConvergenceState.FAST_PATH,
        )
    with pytest.raises(ValueError, match="stop_reason"):
        ConvergenceMetadata(
            refinement_round=1,
            source_attempt_id="attempt-source",
            fresh_attempt_id="attempt-fresh",
            failure_class=FailureClass.SEMANTIC_TEST,
            failure_signature=_fingerprint().failure_signature,
            correction_actor="l1_critic",
            previous_model_identity="gemini:free-3:model-a",
            current_model_identity="gemini:free-3:model-a",
            validator_refs=(),
            convergence_state=ConvergenceState.NON_CONVERGING,
        )


def test_existing_refinement_plan_can_carry_convergence_metadata():
    fingerprint = _fingerprint()
    plan = BoundedRefinementPolicy().plan(_context())
    metadata = ConvergenceMetadata(
        refinement_round=plan.refinement_round,
        source_attempt_id="attempt-source",
        fresh_attempt_id="attempt-fresh",
        failure_class=plan.failure_class,
        failure_signature=fingerprint.failure_signature,
        correction_actor="l1_critic",
        previous_model_identity="gemini:free-3:model-a",
        current_model_identity="gemini:free-3:model-a",
        validator_refs=fingerprint.validator_refs,
        convergence_state=ConvergenceState.REFINEMENT,
    )
    enriched = replace(plan, convergence=metadata)

    assert type(enriched.from_dict(enriched.to_dict()).convergence) is ConvergenceMetadata
    assert enriched.from_dict(enriched.to_dict()) == enriched


def _observation(*, failure_count: int, rung: ValidationRung, signature=None, round=1):
    return ConvergenceObservation(
        failure_count=failure_count,
        validation_rung=rung,
        failure_signature=signature,
        refinement_round=round,
    )


def test_convergence_assessment_marks_lower_failure_count_and_later_rung_as_progress():
    previous = _observation(failure_count=2, rung=ValidationRung.V1, signature=_fingerprint().failure_signature)
    current = _observation(failure_count=1, rung=ValidationRung.V2, signature=_fingerprint(error_code="OTHER").failure_signature)

    assessment = assess_convergence(previous, current)

    assert isinstance(assessment, ConvergenceAssessment)
    assert assessment.state is ConvergenceState.PROGRESS
    assert assessment.failure_count_delta == -1
    assert assessment.validation_advanced is True
    assert assessment.failure_changed is True
    assert assessment.stop_reason is None


def test_convergence_assessment_marks_signature_resolution_as_progress():
    previous = _observation(failure_count=1, rung=ValidationRung.V2, signature=_fingerprint().failure_signature)
    current = _observation(failure_count=0, rung=ValidationRung.V6, signature=None)

    assessment = assess_convergence(previous, current)

    assert assessment.state is ConvergenceState.PROGRESS
    assert assessment.signature_resolved is True


def test_convergence_assessment_distinguishes_no_progress_stuck_and_budget_stop():
    signature = _fingerprint().failure_signature
    previous = _observation(failure_count=1, rung=ValidationRung.V1, signature=signature, round=1)
    same = _observation(failure_count=1, rung=ValidationRung.V1, signature=signature, round=2)

    stuck = assess_convergence(previous, same, same_signature_count=1)
    exhausted = assess_convergence(previous, same, same_signature_count=2)
    budget = assess_convergence(previous, same, same_signature_count=1, max_refinement_rounds=2)

    assert stuck.state is ConvergenceState.STUCK
    assert stuck.stop_reason is None
    assert exhausted.state is ConvergenceState.NON_CONVERGING
    assert exhausted.stop_reason is ConvergenceStopReason.SAME_SIGNATURE_LIMIT
    assert budget.state is ConvergenceState.NON_CONVERGING
    assert budget.stop_reason is ConvergenceStopReason.BUDGET_EXHAUSTED


def test_convergence_assessment_does_not_treat_signature_change_alone_as_progress():
    first_signature = _fingerprint(error_code="FIRST").failure_signature
    second_signature = _fingerprint(error_code="SECOND").failure_signature
    previous = _observation(failure_count=1, rung=ValidationRung.V1, signature=first_signature, round=1)
    current = _observation(failure_count=1, rung=ValidationRung.V1, signature=second_signature, round=2)

    assessment = assess_convergence(previous, current)

    assert assessment.failure_changed is True
    assert assessment.state is ConvergenceState.NO_PROGRESS
    assert "failure_signature_changed" not in assessment.reasons


def test_convergence_assessment_marks_a_b_a_as_recurrent_cycle():
    signature_a = _fingerprint(error_code="CYCLE_A").failure_signature
    signature_b = _fingerprint(error_code="CYCLE_B").failure_signature
    first = _observation(failure_count=1, rung=ValidationRung.V1, signature=signature_a, round=1)
    second = _observation(failure_count=1, rung=ValidationRung.V1, signature=signature_b, round=2)
    current = _observation(failure_count=1, rung=ValidationRung.V1, signature=signature_a, round=3)

    assessment = assess_convergence(second, current, history=(first,))

    assert assessment.state is ConvergenceState.NON_CONVERGING
    assert assessment.stop_reason is ConvergenceStopReason.RECURRENT_CYCLE
    assert "recurrent_failure_cycle" in assessment.reasons


def test_convergence_assessment_round_trips_as_bounded_evidence():
    signature = _fingerprint().failure_signature
    assessment = assess_convergence(
        _observation(failure_count=1, rung=ValidationRung.V1, signature=signature),
        _observation(failure_count=1, rung=ValidationRung.V2, signature=signature, round=2),
    )

    assert ConvergenceAssessment.from_dict(assessment.to_dict()) == assessment
