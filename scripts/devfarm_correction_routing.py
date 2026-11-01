"""Host-owned choice of who should correct a bounded Worker failure.

The existing :class:`BoundedRefinementPolicy` remains the single owner of the
finite next action.  This adapter adds the missing responsibility label used by
the DevFarm composition boundary: a safe Host normalization, the same L1
Worker, an L1 Critic, a distinct same-tier binding, or an upper-tier reframe.
It never calls a model, mutates a Task, approves integration, or bypasses
reconciliation and authority boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from src.dev_agent.intelligence.refinement import (
    BoundedRefinementPolicy,
    FailureClass,
    RefinementAction,
    RefinementContext,
    RefinementPlan,
)


class CorrectionRoutingError(ValueError):
    """A correction responsibility request is malformed."""


@dataclass(frozen=True)
class CorrectionResponsibility:
    """One bounded responsibility assignment for a single failure."""

    responsibility: str
    action: RefinementAction
    reason_code: str
    target_binding_id: str | None
    prompt_contract: str
    model_call_allowed: bool
    host_owned: bool
    refinement_plan: RefinementPlan

    def __post_init__(self) -> None:
        if not isinstance(self.responsibility, str) or not self.responsibility.strip():
            raise CorrectionRoutingError("responsibility must be non-empty text")
        if not isinstance(self.action, RefinementAction):
            raise CorrectionRoutingError("action must be a RefinementAction")
        if not isinstance(self.reason_code, str) or not self.reason_code.strip():
            raise CorrectionRoutingError("reason_code must be non-empty text")
        if self.target_binding_id is not None and (
            not isinstance(self.target_binding_id, str) or not self.target_binding_id.strip()
        ):
            raise CorrectionRoutingError("target_binding_id must be non-empty text when present")
        if not isinstance(self.prompt_contract, str) or not self.prompt_contract.strip():
            raise CorrectionRoutingError("prompt_contract must be non-empty text")
        if not isinstance(self.model_call_allowed, bool) or not isinstance(self.host_owned, bool):
            raise CorrectionRoutingError("responsibility flags must be boolean")
        if not isinstance(self.refinement_plan, RefinementPlan):
            raise CorrectionRoutingError("refinement_plan must be a RefinementPlan")
        if not self.model_call_allowed and self.target_binding_id is not None:
            raise CorrectionRoutingError("non-model responsibility cannot target a binding")

    def to_dict(self) -> dict[str, Any]:
        return {
            "responsibility": self.responsibility,
            "action": self.action.value,
            "reason_code": self.reason_code,
            "target_binding_id": self.target_binding_id,
            "prompt_contract": self.prompt_contract,
            "model_call_allowed": self.model_call_allowed,
            "host_owned": self.host_owned,
            "refinement_plan": self.refinement_plan.to_dict(),
        }


def _failure_class(value: FailureClass | str) -> FailureClass:
    if isinstance(value, FailureClass):
        return value
    if not isinstance(value, str) or not value.strip():
        raise CorrectionRoutingError("failure_class must be a FailureClass or non-empty text")
    try:
        return FailureClass(value.strip().lower().replace("-", "_"))
    except ValueError as exc:
        raise CorrectionRoutingError("failure_class is unsupported") from exc


def _route_from_plan(
    plan: RefinementPlan,
    *,
    host_normalization_available: bool,
) -> tuple[str, str, str, bool, bool]:
    """Map the existing action to a responsibility and safe prompt contract."""

    if plan.action is RefinementAction.RECONCILE:
        return "reconciliation", "reconciliation_required", "no-model-replay", False, True
    if plan.action is RefinementAction.HUMAN:
        return "human_authority", "human_authority_required", "no-model-replay", False, True
    if plan.action is RefinementAction.FAIL:
        return "stop", "bounded_refinement_exhausted", "no-model-replay", False, True
    if plan.action is RefinementAction.CRITIQUE:
        return "l1_critic", "critic_diagnosis", "critic-evidence-only", True, False
    if plan.action is RefinementAction.REASSIGN_SAME_TIER:
        return "alternate_l1", "fresh_binding", "fresh-binding-retry", True, False
    if plan.action is RefinementAction.ESCALATE_TIER:
        return "upper_reframe", "upper_tier_reframe", "bounded-reframe", True, False
    if plan.action is RefinementAction.INCREASE_REASONING:
        return "upper_reframe", "bounded_reasoning_increase", "bounded-reframe", True, False
    if plan.action is RefinementAction.CORRECT:
        if (
            host_normalization_available
            and plan.failure_class is FailureClass.FORMAT_PATCH
        ):
            return "host_normalization", "host_envelope_normalization", "host-envelope-only", False, True
        return "same_l1", "same_binding_correction", "bounded-repair-delta", True, False
    if plan.action is RefinementAction.NONE:
        return "none", "no_failure", "no-model-replay", False, True
    raise CorrectionRoutingError("refinement action is not routable")


def select_correction_responsibility(
    context: RefinementContext,
    failure_class: FailureClass | str,
    *,
    host_normalization_available: bool = False,
) -> CorrectionResponsibility:
    """Select the least expensive safe correction owner.

    Failure-specific diagnostics remain in the existing immutable
    ``RefinementPlan``.  This function only labels who should carry out that
    plan.  In particular, raw provider output or traceback text is never part
    of the returned prompt contract.
    """

    if not isinstance(context, RefinementContext):
        raise CorrectionRoutingError("context must be RefinementContext")
    if not isinstance(host_normalization_available, bool):
        raise CorrectionRoutingError("host_normalization_available must be boolean")
    failure = _failure_class(failure_class)
    plan = BoundedRefinementPolicy().plan(replace(context, failure_class=failure))
    responsibility, reason_code, prompt_contract, model_allowed, host_owned = _route_from_plan(
        plan,
        host_normalization_available=host_normalization_available,
    )
    target_binding = plan.next_binding_id if model_allowed and plan.action is RefinementAction.REASSIGN_SAME_TIER else None
    return CorrectionResponsibility(
        responsibility=responsibility,
        action=plan.action if responsibility != "host_normalization" else RefinementAction.NONE,
        reason_code=reason_code,
        target_binding_id=target_binding,
        prompt_contract=prompt_contract,
        model_call_allowed=model_allowed,
        host_owned=host_owned,
        refinement_plan=plan,
    )


__all__ = [
    "CorrectionResponsibility",
    "CorrectionRoutingError",
    "select_correction_responsibility",
]
