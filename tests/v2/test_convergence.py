from dataclasses import replace
from uuid import uuid4

import pytest

from src.dev_agent.domain.protocol import IntelligenceTier
from src.dev_agent.intelligence.convergence import (
    ConvergenceMetadata,
    ConvergenceState,
    ConvergenceStopReason,
    FailureFingerprint,
    same_failure_signature,
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


def test_fast_path_metadata_is_zero_round_and_has_no_failure_or_extra_work():
    metadata = ConvergenceMetadata.fast_path(current_model_identity="gemini:free-3:model-a")

    assert metadata.convergence_state is ConvergenceState.FAST_PATH
    assert metadata.refinement_round == 0
    assert metadata.failure_class is None
    assert metadata.failure_signature is None
    assert metadata.stop_reason is ConvergenceStopReason.FAST_PATH
    assert metadata.to_dict()["correction_actor"] is None


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
