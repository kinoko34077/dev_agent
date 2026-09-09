from src.dev_agent.domain.protocol import IntelligenceTier
from src.dev_agent.intelligence.coordination import EvaluationCoordinator
from src.dev_agent.intelligence.escalation import EscalationContext, EscalationTarget
from src.dev_agent.intelligence.evaluator import EvaluationEvidence, EvaluatorDecision
from src.dev_agent.state import JsonStateStore


_TASK_ID = "00000000-0000-0000-0000-000000000001"


def _evidence(**overrides):
    values = {
        "task_id": _TASK_ID,
        "attempt": 1,
        "max_attempts": 3,
        "objective_met": False,
        "deterministic_checks_passed": False,
        "policy_compliant": True,
        "external_outcome_known": True,
        "retryable_failure": False,
        "alternate_provider_available": False,
        "human_approval_required": False,
    }
    values.update(overrides)
    return EvaluationEvidence(**values)


def _context(**overrides):
    values = {
        "task_id": _TASK_ID,
        "current_tier": IntelligenceTier.L1,
        "allowed_tiers": (IntelligenceTier.L1, IntelligenceTier.L2),
        "attempt": 1,
        "max_attempts": 3,
        "escalation_count": 0,
        "max_escalations": 1,
        "now_epoch": 100.0,
        "deadline_epoch": 200.0,
        "budget_remaining": 1.0,
        "estimated_cost": 0.1,
        "retryable_failure": False,
        "same_provider_available": False,
        "alternate_provider_available": False,
    }
    values.update(overrides)
    return EscalationContext(**values)


def test_evaluation_coordinator_records_evidence_before_returning_bounded_plan(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")

    cycle = EvaluationCoordinator(store).evaluate_and_plan(_evidence(), escalation_context=_context())

    assert cycle.result.decision is EvaluatorDecision.ESCALATE
    assert cycle.plan is not None
    assert cycle.plan.target is EscalationTarget.HIGHER_TIER
    events = [item for item in store.snapshot()["events"] if item["event_type"] == "evaluation.recorded"]
    assert len(events) == 1
    assert events[0]["payload"]["decision"] == "ESCALATE"
    planned = [item for item in store.snapshot()["events"] if item["event_type"] == "escalation.planned"]
    assert len(planned) == 1
    assert planned[0]["payload"]["target"] == "higher_tier"
    assert cycle.to_dict()["plan"]["next_tier"] == "L2"
    assert cycle.to_dict()["plan_event"]["event_type"] == "escalation.planned"


def test_evaluation_coordinator_can_record_a_terminal_pass_without_planning_dispatch(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")

    cycle = EvaluationCoordinator(store).evaluate_and_plan(
        _evidence(objective_met=True, deterministic_checks_passed=True)
    )

    assert cycle.result.decision is EvaluatorDecision.PASS
    assert cycle.plan is None
    assert cycle.plan_event is None
    assert store.has_event(_TASK_ID, "evaluation.recorded")


def test_evaluation_coordinator_rejects_context_for_a_different_attempt_or_task(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")

    try:
        EvaluationCoordinator(store).evaluate_and_plan(
            _evidence(),
            escalation_context=_context(task_id="other-task"),
        )
    except ValueError as exc:
        assert "task_id" in str(exc)
    else:
        raise AssertionError("mismatched evaluation context was accepted")

    assert store.snapshot()["events"] == []


def test_evaluation_coordinator_requires_explicit_review_before_plan_acceptance(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")
    coordinator = EvaluationCoordinator(store)
    cycle = coordinator.evaluate_and_plan(_evidence(), escalation_context=_context())

    assert not store.has_event(_TASK_ID, "escalation.accepted")
    review = coordinator.review_plan(
        cycle,
        actor="codex-reviewer",
        approved=True,
        approval_reference="review-001",
    )

    assert review.event_type == "escalation.accepted"
    assert review.payload["actor"] == "codex-reviewer"
    assert review.payload["approval_reference"] == "review-001"
    assert review.payload["plan_id"] == cycle.plan.plan_id
    assert review.payload["plan"]["task_id"] == _TASK_ID
    assert store.has_event(_TASK_ID, "escalation.accepted")
    assert not store.has_event(_TASK_ID, "task.completed")


def test_evaluation_coordinator_records_explicit_plan_rejection_with_reason(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")
    coordinator = EvaluationCoordinator(store)
    cycle = coordinator.evaluate_and_plan(_evidence(), escalation_context=_context())

    review = coordinator.review_plan(
        cycle,
        actor="operator",
        approved=False,
        approval_reference="review-002",
        reason="hold for additional evidence",
    )

    assert review.event_type == "escalation.rejected"
    assert review.payload["reason"] == "hold for additional evidence"
    assert store.has_event(_TASK_ID, "escalation.rejected")
    assert not store.has_event(_TASK_ID, "task.completed")


def test_evaluation_coordinator_rejects_review_without_plan_or_rejection_reason(tmp_path):
    store = JsonStateStore(tmp_path / "state.json")
    coordinator = EvaluationCoordinator(store)
    terminal = coordinator.evaluate_and_plan(
        _evidence(objective_met=True, deterministic_checks_passed=True)
    )

    try:
        coordinator.review_plan(
            terminal,
            actor="operator",
            approved=True,
            approval_reference="review-003",
        )
    except ValueError as exc:
        assert "plan" in str(exc)
    else:
        raise AssertionError("terminal evaluation was reviewable as a plan")

    planned = coordinator.evaluate_and_plan(_evidence(), escalation_context=_context())
    try:
        coordinator.review_plan(
            planned,
            actor="operator",
            approved=False,
            approval_reference="review-004",
        )
    except ValueError as exc:
        assert "reason" in str(exc)
    else:
        raise AssertionError("plan rejection without a reason was accepted")
