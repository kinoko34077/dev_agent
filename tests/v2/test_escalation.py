import pytest

from src.dev_agent.domain.protocol import IntelligenceTier
from src.dev_agent.intelligence.escalation import (
    BoundedEscalationPolicy,
    EscalationContext,
    EscalationDispatchRequest,
    EscalationTarget,
)
from src.dev_agent.intelligence.evaluator import EvaluatorDecision


def _context(**overrides):
    values = {
        "task_id": "task-1",
        "current_tier": IntelligenceTier.L1,
        "allowed_tiers": (IntelligenceTier.L1, IntelligenceTier.L2, IntelligenceTier.L3),
        "attempt": 1,
        "max_attempts": 5,
        "escalation_count": 0,
        "max_escalations": 2,
        "now_epoch": 100.0,
        "deadline_epoch": 200.0,
        "budget_remaining": 1.0,
        "estimated_cost": 0.1,
        "retryable_failure": True,
        "same_provider_available": True,
        "alternate_provider_available": True,
    }
    values.update(overrides)
    return EscalationContext(**values)


def test_escalation_uses_same_provider_before_other_provider_or_higher_tier():
    plan = BoundedEscalationPolicy().plan(_context())

    assert plan.decision is EvaluatorDecision.RETRY_SAME
    assert plan.target is EscalationTarget.SAME_PROVIDER
    assert plan.next_tier is None


def test_escalation_uses_other_provider_before_higher_tier():
    plan = BoundedEscalationPolicy().plan(_context(same_provider_available=False))

    assert plan.decision is EvaluatorDecision.RETRY_OTHER_PROVIDER
    assert plan.target is EscalationTarget.OTHER_PROVIDER
    assert plan.next_tier is None


def test_escalation_moves_to_next_allowed_tier_after_provider_options():
    plan = BoundedEscalationPolicy().plan(
        _context(same_provider_available=False, alternate_provider_available=False)
    )

    assert plan.decision is EvaluatorDecision.ESCALATE
    assert plan.target is EscalationTarget.HIGHER_TIER
    assert plan.next_tier is IntelligenceTier.L2


def test_escalation_stops_at_escalation_ceiling():
    plan = BoundedEscalationPolicy().plan(
        _context(
            same_provider_available=False,
            alternate_provider_available=False,
            escalation_count=2,
            max_escalations=2,
        )
    )

    assert plan.decision is EvaluatorDecision.FAIL
    assert plan.target is EscalationTarget.NONE
    assert "escalation_ceiling_reached" in plan.reasons


def test_escalation_fails_at_attempt_or_deadline_boundary():
    at_attempt_limit = BoundedEscalationPolicy().plan(_context(attempt=5, max_attempts=5))
    at_deadline = BoundedEscalationPolicy().plan(_context(now_epoch=200.0))

    assert at_attempt_limit.decision is EvaluatorDecision.FAIL
    assert "attempt_ceiling_reached" in at_attempt_limit.reasons
    assert at_deadline.decision is EvaluatorDecision.FAIL
    assert "deadline_exceeded" in at_deadline.reasons


def test_escalation_waits_for_human_when_budget_cannot_cover_next_dispatch():
    plan = BoundedEscalationPolicy().plan(_context(budget_remaining=0.05, estimated_cost=0.1))

    assert plan.decision is EvaluatorDecision.WAIT_HUMAN
    assert plan.target is EscalationTarget.HUMAN
    assert "budget_insufficient" in plan.reasons


def test_non_retryable_failure_only_escalates_when_a_higher_tier_is_available():
    higher = BoundedEscalationPolicy().plan(_context(retryable_failure=False, same_provider_available=True))
    terminal = BoundedEscalationPolicy().plan(
        _context(
            current_tier=IntelligenceTier.L3,
            allowed_tiers=(IntelligenceTier.L3,),
            retryable_failure=False,
            same_provider_available=True,
            alternate_provider_available=True,
        )
    )

    assert higher.decision is EvaluatorDecision.ESCALATE
    assert higher.next_tier is IntelligenceTier.L2
    assert terminal.decision is EvaluatorDecision.FAIL


def test_escalation_context_rejects_invalid_bounds():
    with pytest.raises(ValueError, match="current_tier"):
        _context(current_tier=IntelligenceTier.L0, allowed_tiers=(IntelligenceTier.L1,))
    with pytest.raises(ValueError, match="deadline"):
        _context(deadline_epoch=99.0)


def test_dispatch_request_rejects_incoherent_decision_and_target():
    with pytest.raises(ValueError, match="decision"):
        EscalationDispatchRequest(
            task_id="task-1",
            plan_id="plan-1",
            decision=EvaluatorDecision.RETRY_SAME,
            target=EscalationTarget.OTHER_PROVIDER,
            next_tier=None,
            approved_by="operator",
            approval_reference="review-1",
        )
