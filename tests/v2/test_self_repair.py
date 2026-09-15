from __future__ import annotations

from dataclasses import replace
from pathlib import Path

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
    RollbackProof,
)
from src.dev_agent.policy.approvals import canonical_arguments_hash
from scripts.devfarm_self_repair import integrate_approved_repair


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
        "rollback_proof": RollbackProof.create(
            revision="a" * 40,
            release_ref=".devfarm/runtime-releases/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            health_status="passed",
            verified_at="2026-09-16T12:00:00+00:00",
        ),
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


def test_rollback_proof_round_trip_binds_revision_release_and_health() -> None:
    proof = _evidence().rollback_proof

    assert isinstance(proof, RollbackProof)
    restored = RollbackProof.from_dict(proof.to_dict())

    assert restored == proof
    assert restored.revision == "a" * 40
    assert restored.release_materialized is True
    assert restored.release_clean is True
    assert restored.health_status == "passed"
    assert len(restored.proof_digest) == 64


def test_rollback_proof_rejects_unverified_release_or_health() -> None:
    with pytest.raises(ValueError, match="release_materialized"):
        RollbackProof.create(
            revision="a" * 40,
            release_ref=".devfarm/runtime-releases/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            release_materialized=False,
            health_status="passed",
            verified_at="2026-09-16T12:00:00+00:00",
        )
    with pytest.raises(ValueError, match="health_status"):
        RollbackProof.create(
            revision="a" * 40,
            release_ref=".devfarm/runtime-releases/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            health_status="failed",
            verified_at="2026-09-16T12:00:00+00:00",
        )


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
        base_revision=candidate.evidence.base_revision,
        patch_sha256=candidate.evidence.patch_sha256,
        manifest_ref=candidate.evidence.manifest_ref,
        verification_ref=candidate.evidence.verification_ref,
        rollback_ref=candidate.evidence.rollback_ref or "",
        rollback_proof_digest=candidate.evidence.rollback_proof.proof_digest if candidate.evidence.rollback_proof else None,
        review_decision_id="review-repair-1",
        target_checkout_ref=str(Path("workspace").resolve()),
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


def test_repair_execution_preflight_rejects_missing_or_mismatched_rollback_proof() -> None:
    candidate_result = RepairPolicy().evaluate(_plan(), _evidence())
    assert candidate_result.candidate is not None
    request = _execution_request(candidate_result.candidate)

    missing = replace(request, rollback_proof_digest=None)
    missing_evaluation = RepairExecutionPolicy().evaluate(
        candidate_result.candidate,
        missing,
        _ApprovalStore(approved=True),
    )
    mismatched = replace(request, rollback_proof_digest="c" * 64)
    mismatch_evaluation = RepairExecutionPolicy().evaluate(
        candidate_result.candidate,
        mismatched,
        _ApprovalStore(approved=True),
    )

    assert missing_evaluation.eligible is False
    assert "rollback_proof_required" in missing_evaluation.reasons
    assert mismatch_evaluation.eligible is False
    assert "rollback_proof_mismatch" in mismatch_evaluation.reasons


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


def test_repair_execution_request_binds_operation_type() -> None:
    candidate_result = RepairPolicy().evaluate(_plan(), _evidence())
    assert candidate_result.candidate is not None
    request = _execution_request(candidate_result.candidate)

    assert request.operation_type == "repair_integration"
    assert request.authorization_arguments()["operation_type"] == "repair_integration"
    assert request.to_dict()["operation_type"] == "repair_integration"

    payload = request.to_dict()
    payload["operation_type"] = "other_operation"
    payload["authorization_arguments_hash"] = request.authorization_hash()
    with pytest.raises(ValueError, match="operation_type"):
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


class _IntegrationApprovalStore(_ApprovalStore):
    def consume_approval(self, approval_id: str, **kwargs: object) -> bool:
        self.consumed = True
        self.calls.append({"consumed_approval_id": approval_id, **kwargs})
        return True


class _RepairRunner:
    run_id = "repair-run-1"

    def __init__(self, candidate: RepairCandidate) -> None:
        self.candidate = candidate
        self.integration_args: dict[str, object] | None = None
        self.plan_data: dict[str, object] = {
            "tasks": [
                {
                    "task_id": "repair-task-1",
                    "owner": "worker",
                    "status": "HOST_VERIFIED",
                    "last_attempt_id": self.candidate.evidence.attempt_id,
                    "verified_patch_digest": self.candidate.evidence.patch_sha256,
                    "manifest_path": self.candidate.evidence.manifest_ref,
                }
            ],
            "review_decisions": [
                {
                    "decision_id": "review-repair-1",
                    "task_id": "repair-task-1",
                    "attempt_id": self.candidate.evidence.attempt_id,
                    "decision": "APPROVE_INTEGRATION",
                }
            ],
        }

    def plan(self) -> dict[str, object]:
        return self.plan_data

    def review_packet(self, task_id: str, *, attempt_id: str) -> dict[str, object]:
        assert task_id == "repair-task-1"
        assert attempt_id == self.candidate.evidence.attempt_id
        return {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "patch_sha256": self.candidate.evidence.patch_sha256,
            "artifact_refs": [
                {"kind": "patch", "path": self.candidate.evidence.patch_ref},
                {"kind": "verification", "path": self.candidate.evidence.verification_ref},
            ],
        }

    def integrate_approved_worker(self, task_id: str, **kwargs: object) -> str:
        self.integration_args = {"task_id": task_id, **kwargs}
        return "host-integration-result"


def test_approved_repair_adapter_rechecks_authority_before_consuming_approval() -> None:
    candidate_result = RepairPolicy().evaluate(_plan(), _evidence())
    assert candidate_result.candidate is not None
    candidate = candidate_result.candidate
    request = _execution_request(candidate)
    runner = _RepairRunner(candidate)
    store = _IntegrationApprovalStore(approved=True)

    result = integrate_approved_repair(
        runner,
        candidate,
        request,
        approval_store=store,
        target_checkout="workspace",
    )

    assert result == "host-integration-result"
    assert store.consumed is True
    assert runner.integration_args == {
        "task_id": "repair-task-1",
        "decision_id": "review-repair-1",
        "commit_message": "apply bounded repair",
        "target_checkout": "workspace",
        "target_ref": "HEAD",
    }


def test_approved_repair_adapter_fails_before_consuming_mismatched_patch() -> None:
    candidate_result = RepairPolicy().evaluate(_plan(), _evidence())
    assert candidate_result.candidate is not None
    candidate = candidate_result.candidate
    request = _execution_request(candidate)
    runner = _RepairRunner(candidate)
    runner.plan_data["tasks"][0]["verified_patch_digest"] = "c" * 64  # type: ignore[index]
    store = _IntegrationApprovalStore(approved=True)

    with pytest.raises(ValueError, match="patch digest"):
        integrate_approved_repair(
            runner,
            candidate,
            request,
            approval_store=store,
            target_checkout="workspace",
        )

    assert store.consumed is False
    assert runner.integration_args is None
