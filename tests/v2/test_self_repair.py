from __future__ import annotations

from dataclasses import replace

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
    RepairExecutionPolicy,
    RepairExecutionRequest,
    RepairPolicy,
)
from src.dev_agent.policy.approvals import canonical_arguments_hash


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


class _ApprovalStore:
    def __init__(self, *, approved: bool) -> None:
        self.approved = approved
        self.calls: list[dict[str, object]] = []
        self.consumed = False

    def has_approval(self, approval_id: str, **kwargs: object) -> bool:
        self.calls.append({"approval_id": approval_id, **kwargs})
        return self.approved

    def consume_approval(self, *args: object, **kwargs: object) -> bool:
        self.consumed = True
        raise AssertionError("approval must not be consumed by the preflight gate")


def _execution_request(candidate: RepairCandidate) -> RepairExecutionRequest:
    return RepairExecutionRequest(
        candidate_id=candidate.candidate_id,
        run_id="repair-run-1",
        task_id="repair-task-1",
        attempt_id=candidate.evidence.attempt_id,
        review_decision_id="review-repair-1",
        target_checkout_ref="workspace",
        target_ref="HEAD",
        commit_message="apply bounded repair",
        approval_id="approval-repair-1",
        call_id="repair-call-1",
    )


def test_repair_execution_preflight_binds_existing_approval_without_consuming_it() -> None:
    candidate_result = RepairPolicy().evaluate(_plan(), _evidence())
    assert candidate_result.candidate is not None
    request = _execution_request(candidate_result.candidate)
    store = _ApprovalStore(approved=True)

    evaluation = RepairExecutionPolicy().evaluate(candidate_result.candidate, request, store)

    assert evaluation.eligible is True
    assert evaluation.status == "READY_FOR_HOST_EXECUTION"
    assert evaluation.integration_authority == "existing_supervisor_host_helper"
    assert evaluation.request == request
    assert store.consumed is False
    assert store.calls == [
        {
            "approval_id": "approval-repair-1",
            "task_id": "repair-task-1",
            "side_effect_level": "external_write",
            "call_id": "repair-call-1",
            "arguments_hash": canonical_arguments_hash(request.authorization_arguments()),
        }
    ]


def test_repair_execution_request_round_trip_checks_authorization_digest() -> None:
    candidate_result = RepairPolicy().evaluate(_plan(), _evidence())
    assert candidate_result.candidate is not None
    request = _execution_request(candidate_result.candidate)

    restored = RepairExecutionRequest.from_dict(request.to_dict())

    assert restored == request
    payload = request.to_dict()
    payload["target_ref"] = "other-ref"
    with pytest.raises(ValueError, match="authorization hash"):
        RepairExecutionRequest.from_dict(payload)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("candidate_id", "candidate_identity_mismatch"),
        ("attempt_id", "attempt_identity_mismatch"),
    ],
)
def test_repair_execution_preflight_fails_closed_on_identity_mismatch(
    change: str, reason: str
) -> None:
    candidate_result = RepairPolicy().evaluate(_plan(), _evidence())
    assert candidate_result.candidate is not None
    request = _execution_request(candidate_result.candidate)
    mismatched = replace(request, **{change: f"different-{change}"})

    evaluation = RepairExecutionPolicy().evaluate(
        candidate_result.candidate,
        mismatched,
        _ApprovalStore(approved=True),
    )

    assert evaluation.eligible is False
    assert evaluation.status == "REJECTED"
    assert reason in evaluation.reasons


def test_repair_execution_preflight_requires_durable_approval() -> None:
    candidate_result = RepairPolicy().evaluate(_plan(), _evidence())
    assert candidate_result.candidate is not None
    request = _execution_request(candidate_result.candidate)

    evaluation = RepairExecutionPolicy().evaluate(
        candidate_result.candidate,
        request,
        _ApprovalStore(approved=False),
    )

    assert evaluation.eligible is False
    assert evaluation.status == "REJECTED"
    assert "approval_missing_or_mismatched" in evaluation.reasons
