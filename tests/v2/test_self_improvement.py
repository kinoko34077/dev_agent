from __future__ import annotations

import json

import pytest

from src.dev_agent.intelligence.self_improvement import (
    ImprovementDiagnosis,
    ImprovementPlanProposal,
    ObservationRecord,
    diagnose_observation,
    propose_improvement,
)


def _observation() -> ObservationRecord:
    return ObservationRecord(
        subject="worker patch verification",
        source="host_verification",
        observed_at="2026-09-14T12:00:00+00:00",
        status="DEGRADED",
        metrics={"attempts": 2, "host_verified": False, "failure_rate": 0.5},
        evidence_refs=(
            {"kind": "verification", "path": ".devfarm/results/task/verification.json"},
        ),
        observation_id="observation-1",
    )


def test_observation_round_trip_is_bounded_and_json_safe() -> None:
    observation = _observation()

    encoded = observation.to_dict()
    decoded = ObservationRecord.from_dict(json.loads(json.dumps(encoded)))

    assert decoded == observation
    assert encoded["evidence_refs"][0]["kind"] == "verification"


def test_observation_rejects_raw_output_and_unbounded_metrics() -> None:
    with pytest.raises(ValueError, match="raw output"):
        ObservationRecord(
            subject="worker",
            source="host",
            observed_at="2026-09-14T12:00:00+00:00",
            status="FAILED",
            metrics={"stdout": "not an evidence reference"},
            evidence_refs=({"kind": "test", "path": "tests/v2/test.py"},),
        )

    with pytest.raises(ValueError, match="scalar"):
        ObservationRecord(
            subject="worker",
            source="host",
            observed_at="2026-09-14T12:00:00+00:00",
            status="FAILED",
            metrics={"nested": {"value": 1}},
            evidence_refs=({"kind": "test", "path": "tests/v2/test.py"},),
        )


def test_diagnosis_preserves_observation_evidence_and_is_proposal_only() -> None:
    observation = _observation()

    diagnosis = diagnose_observation(
        observation,
        category="reliability",
        severity="normal",
        confidence="medium",
        causes=("bounded patch contract was not satisfied",),
        recommended_focus=("improve worker output contract",),
    )

    assert isinstance(diagnosis, ImprovementDiagnosis)
    assert diagnosis.observation_id == observation.observation_id
    assert diagnosis.evidence_refs == observation.evidence_refs
    assert diagnosis.status == "PROPOSAL_ONLY"

    with pytest.raises(ValueError, match="grounded"):
        diagnose_observation(
            observation,
            category="reliability",
            severity="normal",
            confidence="medium",
            evidence_refs=({"kind": "invented", "path": "outside.json"},),
        )


def test_improvement_plan_is_bounded_and_never_authorized_for_execution() -> None:
    observation = _observation()
    diagnosis = diagnose_observation(
        observation,
        category="reliability",
        severity="normal",
        confidence="medium",
        causes=("patch contract failure",),
        recommended_focus=("worker prompt",),
        diagnosis_id="diagnosis-1",
    )

    plan = propose_improvement(
        observations=(observation,),
        diagnoses=(diagnosis,),
        objective="reduce malformed worker patch proposals",
        steps=("add a bounded syntax-completeness prompt check",),
        acceptance=("existing Host Verification remains fail-closed",),
        exclusions=("do not relax patch or protected-path validation",),
        risk="normal",
        plan_id="improvement-plan-1",
    )

    assert isinstance(plan, ImprovementPlanProposal)
    assert plan.status == "PROPOSAL_ONLY"
    assert plan.requires_human_approval is True
    assert "dispatch" not in plan.to_dict()
    assert "integration" not in plan.to_dict()
    assert plan.evidence_refs == observation.evidence_refs

    with pytest.raises(ValueError, match="human approval"):
        ImprovementPlanProposal(
            observation_ids=(observation.observation_id,),
            diagnosis_ids=(diagnosis.diagnosis_id,),
            objective="bounded proposal",
            steps=("one step",),
            acceptance=("one check",),
            exclusions=("one exclusion",),
            risk="low",
            evidence_refs=observation.evidence_refs,
            requires_human_approval=False,
        )


def test_improvement_plan_rejects_unrelated_diagnosis() -> None:
    observation = _observation()
    other = ObservationRecord(
        subject="other",
        source="host_verification",
        observed_at="2026-09-14T12:00:00+00:00",
        status="FAILED",
        metrics={"attempts": 1},
        evidence_refs=observation.evidence_refs,
        observation_id="observation-2",
    )
    diagnosis = diagnose_observation(
        other,
        category="verification",
        severity="high",
        confidence="high",
        causes=("verification failed",),
        recommended_focus=("inspect test evidence",),
    )

    with pytest.raises(ValueError, match="observation"):
        propose_improvement(
            observations=(observation,),
            diagnoses=(diagnosis,),
            objective="bounded proposal",
            steps=("one step",),
            acceptance=("one check",),
            exclusions=("one exclusion",),
            risk="normal",
        )
