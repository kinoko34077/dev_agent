from __future__ import annotations

import pytest

from src.dev_agent.intelligence.self_improvement import (
    ImprovementDiagnosis,
    ImprovementPlanProposal,
    ObservationRecord,
    diagnose_observation,
    propose_improvement,
)
from src.dev_agent.intelligence.self_repair import (
    RepairCandidate,
    RepairEvidence,
    RepairPolicy,
)


def _plan() -> ImprovementPlanProposal:
    observation = ObservationRecord(
        subject="bounded host observation",
        source="devfarm_supervisor",
        observed_at="2026-09-14T12:00:00+00:00",
        status="FAILED",
        metrics={"attempts": 2},
        evidence_refs=({"kind": "verification", "path": ".devfarm/results/task/verification.json"},),
        observation_id="observation-repair-1",
    )
    diagnosis = diagnose_observation(
        observation,
        category="reliability",
        severity="normal",
        confidence="high",
        causes=("bounded worker contract failure",),
        recommended_focus=("repair candidate validation",),
        diagnosis_id="diagnosis-repair-1",
    )
    return propose_improvement(
        observations=(observation,),
        diagnoses=(diagnosis,),
        objective="prepare a bounded repair candidate",
        steps=("validate the candidate patch independently",),
        acceptance=("preserve protected-path rejection",),
        exclusions=("do not auto-integrate",),
        risk="low",
        plan_id="improvement-plan-repair-1",
    )


def _evidence(**overrides: object) -> RepairEvidence:
    values: dict[str, object] = {
        "plan_id": "improvement-plan-repair-1",
        "base_revision": "a" * 40,
        "attempt_id": "attempt-repair-1",
        "patch_ref": ".devfarm/results/task/attempts/attempt-repair-1/patch.diff",
        "patch_sha256": "b" * 64,
        "manifest_ref": ".devfarm/tasks/task.json",
        "verification_ref": ".devfarm/results/task/attempts/attempt-repair-1/verification.json",
        "changed_files": ("src/example.py",),
        "verification_status": "passed",
        "verification_trust_level": "TRUSTED_HOST_EXEC",
        "operator_approved": True,
        "independent_verification": True,
        "external_outcome_known": True,
        "rollback_ref": "git:last-known-good",
    }
    values.update(overrides)
    return RepairEvidence(**values)


def test_repair_policy_returns_proposal_only_candidate() -> None:
    result = RepairPolicy().evaluate(_plan(), _evidence())

    assert result.eligible is True
    assert result.status == "CANDIDATE"
    assert isinstance(result.candidate, RepairCandidate)
    assert result.candidate.status == "PROPOSAL_ONLY"
    assert result.candidate.requires_human_approval is True
    assert result.candidate.integration_performed is False
    assert result.integration_authority == "host_policy"
    assert "patch" not in result.candidate.to_dict()


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("external_outcome_known", False, "external_outcome_unknown"),
        ("independent_verification", False, "independent_verification_required"),
        ("operator_approved", False, "operator_approval_required"),
        ("rollback_ref", None, "rollback_path_missing"),
        ("changed_files", ("scripts/devfarm.py",), "protected_path"),
    ],
)
def test_repair_policy_fails_closed_on_unsafe_or_incomplete_evidence(
    field: str, value: object, reason: str
) -> None:
    result = RepairPolicy().evaluate(_plan(), _evidence(**{field: value}))

    assert result.eligible is False
    assert result.status == "REJECTED"
    assert reason in result.reasons
    assert result.candidate is None
    assert result.integration_authority == "codex_and_host"


def test_repair_policy_does_not_accept_non_proposal_plan() -> None:
    plan = _plan()
    payload = plan.to_dict()
    payload["status"] = "EXECUTABLE"

    with pytest.raises(ValueError, match="status"):
        ImprovementPlanProposal.from_dict(payload)


def test_repair_evidence_round_trip_is_bounded() -> None:
    evidence = _evidence()
    restored = RepairEvidence.from_dict(evidence.to_dict())

    assert restored == evidence
    assert "patch" not in restored.to_dict()


def test_repair_evidence_rejects_raw_patch_or_secret_fields() -> None:
    with pytest.raises(ValueError, match="patch"):
        RepairEvidence.from_dict({**_evidence().to_dict(), "patch": "raw"})

    with pytest.raises(ValueError, match="secret"):
        RepairEvidence.from_dict({**_evidence().to_dict(), "api_key": "secret"})
