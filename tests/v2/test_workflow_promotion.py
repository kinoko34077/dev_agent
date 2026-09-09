import pytest

from src.dev_agent.intelligence.workflow import (
    WorkflowPromotionCandidate,
    WorkflowPromotionCoordinator,
    WorkflowPromotionDecision,
    WorkflowPromotionEvidence,
)
from src.dev_agent.state import JsonStateStore


_TASK_ID = "00000000-0000-0000-0000-000000000011"


def _evidence(**overrides):
    values = {
        "workflow_key": "dependency-update",
        "task_id": _TASK_ID,
        "successful_runs": 3,
        "required_successes": 3,
        "deterministic_checks_passed": True,
        "policy_compliant": True,
        "security_reviewed": True,
        "budget_quota_reviewed": True,
        "rollback_defined": True,
        "rollback_ref": "rollback/dependency-update",
        "operator_owner": "codex-operator",
        "manifest_ref": "manifests/dependency-update.json",
    }
    values.update(overrides)
    return WorkflowPromotionEvidence(**values)


def test_workflow_promotion_requires_repeated_host_evidence_and_emits_only_a_candidate(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")

    cycle = WorkflowPromotionCoordinator(store).evaluate_and_propose(_evidence())

    assert cycle.result.decision is WorkflowPromotionDecision.ELIGIBLE
    assert isinstance(cycle.candidate, WorkflowPromotionCandidate)
    assert cycle.candidate.requires_human_review is True
    assert cycle.proposal_event.event_type == "workflow.promotion.proposed"
    assert store.has_event(_TASK_ID, "workflow.promotion.evaluated")
    assert store.has_event(_TASK_ID, "workflow.promotion.proposed")
    assert not store.has_event(_TASK_ID, "workflow.promoted")


def test_workflow_promotion_records_ineligible_evidence_without_proposal(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")

    cycle = WorkflowPromotionCoordinator(store).evaluate_and_propose(
        _evidence(
            successful_runs=2,
            security_reviewed=False,
            rollback_defined=False,
            rollback_ref=None,
        )
    )

    assert cycle.result.decision is WorkflowPromotionDecision.NOT_ELIGIBLE
    assert cycle.candidate is None
    assert cycle.proposal_event is None
    assert "success_history_insufficient" in cycle.result.reasons
    assert "security_review_missing" in cycle.result.reasons
    assert "rollback_path_missing" in cycle.result.reasons
    assert not store.has_event(_TASK_ID, "workflow.promotion.proposed")


def test_workflow_promotion_review_is_explicit_and_does_not_activate_workflow(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")
    coordinator = WorkflowPromotionCoordinator(store)
    cycle = coordinator.evaluate_and_propose(_evidence())

    review = coordinator.review_candidate(
        cycle,
        actor="operator",
        approved=True,
        approval_reference="workflow-review-001",
    )

    assert review.event_type == "workflow.promotion.reviewed"
    assert review.payload["review"] == "accepted"
    assert review.payload["candidate_id"] == cycle.candidate.candidate_id
    assert review.payload["activation"] == "not_performed"
    assert not store.has_event(_TASK_ID, "workflow.promoted")


def test_workflow_promotion_rejects_review_without_candidate_or_reason(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")
    coordinator = WorkflowPromotionCoordinator(store)
    ineligible = coordinator.evaluate_and_propose(_evidence(successful_runs=1))

    with pytest.raises(ValueError, match="candidate"):
        coordinator.review_candidate(
            ineligible,
            actor="operator",
            approved=True,
            approval_reference="workflow-review-002",
        )

    eligible = coordinator.evaluate_and_propose(_evidence())
    with pytest.raises(ValueError, match="reason"):
        coordinator.review_candidate(
            eligible,
            actor="operator",
            approved=False,
            approval_reference="workflow-review-003",
        )
