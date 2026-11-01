from __future__ import annotations

from typing import Any

import pytest

from scripts.devfarm_correction_routing import (
    CorrectionResponsibility,
    select_correction_responsibility,
)
from src.dev_agent.domain.protocol import IntelligenceTier
from src.dev_agent.intelligence.refinement import FailureClass, RefinementAction, RefinementContext


def _context(**overrides: Any) -> RefinementContext:
    values: dict[str, Any] = {
        "task_id": "task-1",
        "failure_class": None,
        "current_tier": IntelligenceTier.L1,
        "allowed_tiers": (IntelligenceTier.L1, IntelligenceTier.L2),
        "attempt": 1,
        "max_attempts": 4,
        "refinement_round": 0,
        "max_refinement_rounds": 2,
        "escalation_count": 0,
        "max_escalations": 2,
        "now_epoch": 10.0,
        "deadline_epoch": 100.0,
        "budget_remaining": 1.0,
        "estimated_cost": 0.1,
        "external_outcome_known": True,
        "correction_available": True,
        "critic_available": True,
        "alternate_binding_id": "ollama:worker:fallback",
        "alternate_binding_ids": ("ollama:worker:fallback",),
        "current_reasoning_effort": "minimal",
        "allowed_reasoning_efforts": ("minimal", "low", "medium"),
        "model_change_used": False,
        "reasoning_escalated": False,
    }
    values.update(overrides)
    return RefinementContext(**values)


def test_format_failure_can_be_handled_by_host_without_model_call() -> None:
    decision = select_correction_responsibility(
        _context(),
        FailureClass.FORMAT_PATCH,
        host_normalization_available=True,
    )

    assert isinstance(decision, CorrectionResponsibility)
    assert decision.responsibility == "host_normalization"
    assert decision.action is RefinementAction.NONE
    assert decision.model_call_allowed is False
    assert decision.prompt_contract == "host-envelope-only"


def test_format_failure_without_safe_normalization_stays_same_l1() -> None:
    decision = select_correction_responsibility(
        _context(),
        FailureClass.FORMAT_PATCH,
        host_normalization_available=False,
    )

    assert decision.responsibility == "same_l1"
    assert decision.action is RefinementAction.CORRECT
    assert decision.model_call_allowed is True
    assert decision.target_binding_id is None
    assert decision.prompt_contract == "bounded-repair-delta"


def test_semantic_failure_prefers_critic_over_full_rewrite() -> None:
    decision = select_correction_responsibility(
        _context(),
        FailureClass.SEMANTIC_TEST,
    )

    assert decision.responsibility == "l1_critic"
    assert decision.action is RefinementAction.CRITIQUE
    assert decision.prompt_contract == "critic-evidence-only"
    assert decision.model_call_allowed is True


def test_provider_failure_uses_distinct_binding_not_same_model_repair() -> None:
    decision = select_correction_responsibility(
        _context(),
        FailureClass.PROVIDER_TRANSPORT,
    )

    assert decision.responsibility == "alternate_l1"
    assert decision.action is RefinementAction.REASSIGN_SAME_TIER
    assert decision.target_binding_id == "ollama:worker:fallback"
    assert decision.prompt_contract == "fresh-binding-retry"


def test_capability_failure_can_escalate_to_upper_reframe_after_lower_options() -> None:
    decision = select_correction_responsibility(
        _context(
            alternate_binding_id=None,
            alternate_binding_ids=(),
            correction_available=False,
            critic_available=False,
        ),
        FailureClass.CAPABILITY_REASONING,
    )

    assert decision.responsibility == "upper_reframe"
    assert decision.action is RefinementAction.INCREASE_REASONING
    assert decision.prompt_contract == "bounded-reframe"
    assert decision.model_call_allowed is True


@pytest.mark.parametrize(
    ("failure", "expected_responsibility", "expected_action"),
    [
        (FailureClass.UNKNOWN_EXTERNAL_EFFECT, "reconciliation", RefinementAction.RECONCILE),
        (FailureClass.SECURITY_EGRESS_AUTHORITY, "human_authority", RefinementAction.HUMAN),
    ],
)
def test_safety_failures_never_become_model_repair(
    failure: FailureClass,
    expected_responsibility: str,
    expected_action: RefinementAction,
) -> None:
    decision = select_correction_responsibility(_context(), failure)

    assert decision.responsibility == expected_responsibility
    assert decision.action is expected_action
    assert decision.model_call_allowed is False
    assert decision.target_binding_id is None


def test_unknown_failure_value_fails_closed() -> None:
    with pytest.raises(ValueError, match="failure_class"):
        select_correction_responsibility(_context(), "invented")
