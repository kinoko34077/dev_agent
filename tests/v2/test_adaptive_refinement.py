import pytest

from src.dev_agent.domain.protocol import IntelligenceTier
from src.dev_agent.intelligence.refinement import (
    BoundedRefinementPolicy,
    CriticFinding,
    FailureClass,
    RefinementAction,
    RefinementContext,
    RefinementProposal,
)


def _context(**overrides):
    values = {
        "task_id": "task-refine-1",
        "failure_class": FailureClass.FORMAT_PATCH,
        "current_tier": IntelligenceTier.L1,
        "allowed_tiers": (IntelligenceTier.L1, IntelligenceTier.L2),
        "attempt": 1,
        "max_attempts": 6,
        "refinement_round": 0,
        "max_refinement_rounds": 2,
        "escalation_count": 0,
        "max_escalations": 2,
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


def test_first_pass_has_no_refinement_action():
    plan = BoundedRefinementPolicy().plan(_context(failure_class=None))

    assert plan.action is RefinementAction.NONE
    assert plan.refinement_round == 0
    assert plan.next_binding_id is None
    assert "no_failure" in plan.reasons


def test_format_failure_prefers_correction_without_thinking_escalation():
    plan = BoundedRefinementPolicy().plan(_context())

    assert plan.action is RefinementAction.CORRECT
    assert plan.refinement_round == 1
    assert plan.next_reasoning_effort is None
    assert plan.next_tier is None


def test_semantic_failure_uses_critic_then_correction():
    critic = BoundedRefinementPolicy().plan(
        _context(failure_class=FailureClass.SEMANTIC_TEST)
    )
    correction = BoundedRefinementPolicy().plan(
        _context(
            failure_class=FailureClass.SEMANTIC_TEST,
            refinement_round=1,
            critic_available=False,
        )
    )

    assert critic.action is RefinementAction.CRITIQUE
    assert correction.action is RefinementAction.CORRECT
    assert correction.refinement_round == 2


def test_provider_failure_reassigns_same_tier_without_tier_or_effort_change():
    plan = BoundedRefinementPolicy().plan(
        _context(
            failure_class=FailureClass.PROVIDER_TRANSPORT,
            correction_available=False,
            critic_available=False,
            alternate_binding_id="gemini:worker:free-2",
        )
    )

    assert plan.action is RefinementAction.REASSIGN_SAME_TIER
    assert plan.next_binding_id == "gemini:worker:free-2"
    assert plan.next_tier is None
    assert plan.next_reasoning_effort is None


def test_capability_escalates_one_axis_at_a_time():
    model = BoundedRefinementPolicy().plan(
        _context(
            failure_class=FailureClass.CAPABILITY_REASONING,
            correction_available=False,
            critic_available=False,
            alternate_binding_id="gemini:worker:free-2",
        )
    )
    thinking = BoundedRefinementPolicy().plan(
        _context(
            failure_class=FailureClass.CAPABILITY_REASONING,
            correction_available=False,
            critic_available=False,
            alternate_binding_id=None,
        )
    )
    tier = BoundedRefinementPolicy().plan(
        _context(
            failure_class=FailureClass.CAPABILITY_REASONING,
            correction_available=False,
            critic_available=False,
            alternate_binding_id=None,
            current_reasoning_effort="low",
            reasoning_escalated=True,
        )
    )

    assert model.action is RefinementAction.REASSIGN_SAME_TIER
    assert model.next_reasoning_effort is None
    assert thinking.action is RefinementAction.INCREASE_REASONING
    assert thinking.next_reasoning_effort == "low"
    assert tier.action is RefinementAction.ESCALATE_TIER
    assert tier.next_tier is IntelligenceTier.L2


def test_unknown_and_security_failures_do_not_fail_over_to_another_model():
    unknown = BoundedRefinementPolicy().plan(
        _context(
            failure_class=FailureClass.UNKNOWN_EXTERNAL_EFFECT,
            external_outcome_known=False,
            alternate_binding_id="other-binding",
        )
    )
    security = BoundedRefinementPolicy().plan(
        _context(
            failure_class=FailureClass.SECURITY_EGRESS_AUTHORITY,
            alternate_binding_id="other-binding",
            current_tier=IntelligenceTier.L1,
        )
    )

    assert unknown.action is RefinementAction.RECONCILE
    assert unknown.requires_reconciliation is True
    assert security.action is RefinementAction.HUMAN
    assert security.next_binding_id is None


def test_bounds_stop_refinement_before_another_model_call():
    exhausted = BoundedRefinementPolicy().plan(
        _context(
            refinement_round=2,
            correction_available=True,
            critic_available=True,
            alternate_binding_id="other-binding",
        )
    )
    over_budget = BoundedRefinementPolicy().plan(
        _context(budget_remaining=0.01, estimated_cost=0.1)
    )

    assert exhausted.action is RefinementAction.REASSIGN_SAME_TIER
    assert over_budget.action is RefinementAction.HUMAN
    assert "budget_insufficient" in over_budget.reasons


def test_critic_proposal_is_bounded_and_has_no_integration_authority():
    proposal = RefinementProposal(
        task_id="task-refine-1",
        attempt_id="attempt-1",
        findings=(
            CriticFinding(
                location="src/example.py:10",
                problem="delimiter is unbalanced",
                required_correction="close the conditional block",
            ),
        ),
        evidence_refs=("artifact://failure-1",),
    )

    restored = RefinementProposal.from_dict(proposal.to_dict())

    assert restored == proposal
    assert "decision" not in restored.to_dict()
    assert "integration" not in restored.to_dict()
    with pytest.raises(ValueError, match="secret"):
        CriticFinding(
            location="src/example.py:1",
            problem="api_key is present",
            required_correction="remove it",
        )
