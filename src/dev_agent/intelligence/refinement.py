"""Bounded, Host-owned refinement decisions for Worker failures.

This module only describes one next action.  It never calls a model, selects a
Provider, mutates a Task, approves integration, or replays an external effect.
The caller must persist the returned plan and use the existing Commander,
Provider, REWORK, and Host Verification boundaries for any later action.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import json
import math
from typing import Any
from uuid import UUID, uuid4

from ..coordination.protocol_helpers import ensure_json_safe, ensure_secret_free, validate_identifier, validate_text
from ..domain.protocol import IntelligenceTier
from .convergence import ConvergenceMetadata


class FailureClass(str, Enum):
    """Host-observed cause family used to choose one bounded next action."""

    FORMAT_PATCH = "format_patch"
    SEMANTIC_TEST = "semantic_test"
    CAPABILITY_REASONING = "capability_reasoning"
    PROVIDER_TRANSPORT = "provider_transport"
    UNKNOWN_EXTERNAL_EFFECT = "unknown_external_effect"
    SECURITY_EGRESS_AUTHORITY = "security_egress_authority"


class RefinementAction(str, Enum):
    """A single next action; action execution belongs to existing Host APIs."""

    NONE = "none"
    CORRECT = "correct"
    CRITIQUE = "critique"
    REASSIGN_SAME_TIER = "reassign_same_tier"
    INCREASE_REASONING = "increase_reasoning"
    ESCALATE_TIER = "escalate_tier"
    RECONCILE = "reconcile"
    HUMAN = "human"
    FAIL = "fail"


_REASONING_ORDER = ("minimal", "low", "medium", "high")
_AUTOMATIC_REASONING_CEILING = "medium"
_TIER_ORDER = (IntelligenceTier.L0, IntelligenceTier.L1, IntelligenceTier.L2, IntelligenceTier.L3)
_MAX_FINDINGS = 32
_MAX_EVIDENCE_REFS = 64
_MAX_REASONS = 16
_MAX_PLAN_BYTES = 32 * 1024
_MAX_PROPOSAL_BYTES = 64 * 1024


def _bounded_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def _bounded_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    return float(value)


def _ordered_unique_tiers(value: Any, name: str) -> tuple[IntelligenceTier, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise ValueError(f"{name} must be a non-empty sequence")
    result = tuple(item if isinstance(item, IntelligenceTier) else IntelligenceTier(item) for item in value)
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicates")
    positions = [_TIER_ORDER.index(item) for item in result]
    if positions != sorted(positions):
        raise ValueError(f"{name} must be ordered from lower to higher tier")
    return result


def _ordered_unique_efforts(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise ValueError(f"{name} must be a non-empty sequence")
    result = tuple(validate_identifier(item, f"{name}[]").lower() for item in value)
    if any(item not in _REASONING_ORDER for item in result):
        raise ValueError(f"{name} contains an unsupported reasoning effort")
    if len(set(result)) != len(result):
        raise ValueError(f"{name} must not contain duplicates")
    positions = [_REASONING_ORDER.index(item) for item in result]
    if positions != sorted(positions):
        raise ValueError(f"{name} must be ordered from lower to higher effort")
    return result


@dataclass(frozen=True)
class RefinementContext:
    """Immutable Host observations and ceilings for one next action."""

    task_id: str
    failure_class: FailureClass | None
    current_tier: IntelligenceTier
    allowed_tiers: tuple[IntelligenceTier, ...]
    attempt: int
    max_attempts: int
    refinement_round: int
    max_refinement_rounds: int
    escalation_count: int
    max_escalations: int
    now_epoch: float
    deadline_epoch: float
    budget_remaining: float
    estimated_cost: float
    external_outcome_known: bool
    correction_available: bool
    critic_available: bool
    alternate_binding_id: str | None
    current_reasoning_effort: str
    allowed_reasoning_efforts: tuple[str, ...]
    model_change_used: bool
    reasoning_escalated: bool
    alternate_binding_ids: tuple[str, ...] = field(default_factory=tuple)
    allow_high_reasoning: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", validate_identifier(self.task_id, "task_id"))
        if self.failure_class is not None and not isinstance(self.failure_class, FailureClass):
            try:
                object.__setattr__(self, "failure_class", FailureClass(self.failure_class))
            except (TypeError, ValueError) as exc:
                raise ValueError("failure_class must be a FailureClass or None") from exc
        if not isinstance(self.current_tier, IntelligenceTier):
            try:
                object.__setattr__(self, "current_tier", IntelligenceTier(self.current_tier))
            except (TypeError, ValueError) as exc:
                raise ValueError("current_tier must be an IntelligenceTier") from exc
        tiers = _ordered_unique_tiers(self.allowed_tiers, "allowed_tiers")
        object.__setattr__(self, "allowed_tiers", tiers)
        if self.current_tier not in tiers:
            raise ValueError("current_tier must be included in allowed_tiers")

        for name, value, minimum in (
            ("attempt", self.attempt, 1),
            ("max_attempts", self.max_attempts, 1),
            ("refinement_round", self.refinement_round, 0),
            ("max_refinement_rounds", self.max_refinement_rounds, 0),
            ("escalation_count", self.escalation_count, 0),
            ("max_escalations", self.max_escalations, 0),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if self.attempt > self.max_attempts:
            raise ValueError("attempt cannot exceed max_attempts")
        if self.refinement_round > self.max_refinement_rounds:
            raise ValueError("refinement_round cannot exceed max_refinement_rounds")
        if self.escalation_count > self.max_escalations:
            raise ValueError("escalation_count cannot exceed max_escalations")

        now = _bounded_number(self.now_epoch, "now_epoch")
        deadline = _bounded_number(self.deadline_epoch, "deadline_epoch")
        budget = _bounded_number(self.budget_remaining, "budget_remaining")
        estimated = _bounded_number(self.estimated_cost, "estimated_cost")
        if deadline < now:
            raise ValueError("deadline_epoch must not precede now_epoch")
        if budget < 0 or estimated < 0:
            raise ValueError("budget values must be non-negative")
        object.__setattr__(self, "now_epoch", now)
        object.__setattr__(self, "deadline_epoch", deadline)
        object.__setattr__(self, "budget_remaining", budget)
        object.__setattr__(self, "estimated_cost", estimated)

        for name, value in (
            ("external_outcome_known", self.external_outcome_known),
            ("correction_available", self.correction_available),
            ("critic_available", self.critic_available),
            ("model_change_used", self.model_change_used),
            ("reasoning_escalated", self.reasoning_escalated),
            ("allow_high_reasoning", self.allow_high_reasoning),
        ):
            _bounded_bool(value, name)

        efforts = _ordered_unique_efforts(self.allowed_reasoning_efforts, "allowed_reasoning_efforts")
        current_effort = validate_identifier(self.current_reasoning_effort, "current_reasoning_effort").lower()
        if current_effort not in _REASONING_ORDER:
            raise ValueError("current_reasoning_effort is unsupported")
        if current_effort not in efforts:
            raise ValueError("current_reasoning_effort must be included in allowed_reasoning_efforts")
        object.__setattr__(self, "current_reasoning_effort", current_effort)
        object.__setattr__(self, "allowed_reasoning_efforts", efforts)

        if self.alternate_binding_id is not None:
            object.__setattr__(self, "alternate_binding_id", validate_identifier(self.alternate_binding_id, "alternate_binding_id"))
        if isinstance(self.alternate_binding_ids, (str, bytes)) or not isinstance(self.alternate_binding_ids, Sequence):
            raise ValueError("alternate_binding_ids must be a sequence")
        alternate_ids = tuple(
            validate_identifier(item, "alternate_binding_ids[]")
            for item in self.alternate_binding_ids
        )
        if len(set(alternate_ids)) != len(alternate_ids):
            raise ValueError("alternate_binding_ids must not contain duplicates")
        if self.alternate_binding_id is not None and self.alternate_binding_id not in alternate_ids:
            alternate_ids = (self.alternate_binding_id, *alternate_ids)
        if self.alternate_binding_id is None and alternate_ids:
            object.__setattr__(self, "alternate_binding_id", alternate_ids[0])
        object.__setattr__(self, "alternate_binding_ids", alternate_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "failure_class": self.failure_class.value if self.failure_class is not None else None,
            "current_tier": self.current_tier.value,
            "allowed_tiers": [item.value for item in self.allowed_tiers],
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "refinement_round": self.refinement_round,
            "max_refinement_rounds": self.max_refinement_rounds,
            "escalation_count": self.escalation_count,
            "max_escalations": self.max_escalations,
            "now_epoch": self.now_epoch,
            "deadline_epoch": self.deadline_epoch,
            "budget_remaining": self.budget_remaining,
            "estimated_cost": self.estimated_cost,
            "external_outcome_known": self.external_outcome_known,
            "correction_available": self.correction_available,
            "critic_available": self.critic_available,
            "alternate_binding_id": self.alternate_binding_id,
            "alternate_binding_ids": list(self.alternate_binding_ids),
            "current_reasoning_effort": self.current_reasoning_effort,
            "allowed_reasoning_efforts": list(self.allowed_reasoning_efforts),
            "model_change_used": self.model_change_used,
            "reasoning_escalated": self.reasoning_escalated,
            "allow_high_reasoning": self.allow_high_reasoning,
        }


@dataclass(frozen=True)
class CriticFinding:
    """A short correction finding; it contains no decision authority."""

    location: str
    problem: str
    required_correction: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "location", validate_text(self.location, "location", max_chars=512))
        object.__setattr__(self, "problem", validate_text(self.problem, "problem", max_chars=2_000))
        object.__setattr__(self, "required_correction", validate_text(self.required_correction, "required_correction", max_chars=2_000))
        ensure_secret_free(self.to_dict(), "critic finding")
        ensure_json_safe(self.to_dict(), "critic finding")

    def to_dict(self) -> dict[str, str]:
        return {
            "location": self.location,
            "problem": self.problem,
            "required_correction": self.required_correction,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CriticFinding":
        if not isinstance(value, Mapping):
            raise ValueError("critic finding must be an object")
        return cls(
            location=value.get("location"),
            problem=value.get("problem"),
            required_correction=value.get("required_correction"),
        )


@dataclass(frozen=True)
class RefinementProposal:
    """Bounded Critic output; integration and approval fields are forbidden."""

    task_id: str
    attempt_id: str
    findings: tuple[CriticFinding, ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", validate_identifier(self.task_id, "task_id"))
        object.__setattr__(self, "attempt_id", validate_identifier(self.attempt_id, "attempt_id"))
        if isinstance(self.findings, (str, bytes)) or not isinstance(self.findings, Sequence):
            raise ValueError("findings must be a sequence of CriticFinding objects")
        findings = tuple(self.findings)
        if len(findings) > _MAX_FINDINGS or any(not isinstance(item, CriticFinding) for item in findings):
            raise ValueError("findings must contain at most 32 CriticFinding objects")
        object.__setattr__(self, "findings", findings)
        if isinstance(self.evidence_refs, (str, bytes)) or not isinstance(self.evidence_refs, Sequence):
            raise ValueError("evidence_refs must be a sequence of strings")
        if len(self.evidence_refs) > _MAX_EVIDENCE_REFS:
            raise ValueError("evidence_refs must contain at most 64 items")
        refs = tuple(validate_text(item, "evidence_ref", max_chars=1_024) for item in self.evidence_refs)
        object.__setattr__(self, "evidence_refs", refs)
        ensure_secret_free(self.to_dict(), "refinement proposal")
        encoded = ensure_json_safe(self.to_dict(), "refinement proposal")
        if len(json.dumps(encoded, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > _MAX_PROPOSAL_BYTES:
            raise ValueError("refinement proposal exceeds its size bound")

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "findings": [item.to_dict() for item in self.findings],
            "evidence_refs": list(self.evidence_refs),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RefinementProposal":
        if not isinstance(value, Mapping):
            raise ValueError("refinement proposal must be an object")
        allowed = {"task_id", "attempt_id", "findings", "evidence_refs"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown refinement proposal field: {sorted(unknown)[0]}")
        findings = value.get("findings", ())
        if isinstance(findings, (str, bytes)) or not isinstance(findings, Sequence):
            raise ValueError("findings must be a sequence")
        refs = value.get("evidence_refs", ())
        if isinstance(refs, (str, bytes)) or not isinstance(refs, Sequence):
            raise ValueError("evidence_refs must be a sequence")
        return cls(
            task_id=value.get("task_id"),
            attempt_id=value.get("attempt_id"),
            findings=tuple(CriticFinding.from_dict(item) for item in findings),
            evidence_refs=tuple(refs),
        )


@dataclass(frozen=True)
class RefinementPlan:
    """Durable identity and one-axis action selected by the Host policy."""

    task_id: str
    failure_class: FailureClass | None
    action: RefinementAction
    attempt: int
    refinement_round: int
    next_binding_id: str | None = None
    next_reasoning_effort: str | None = None
    next_tier: IntelligenceTier | None = None
    requires_reconciliation: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)
    convergence: ConvergenceMetadata | None = None
    plan_id: str = field(default_factory=lambda: str(uuid4()))

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", validate_identifier(self.task_id, "task_id"))
        if self.failure_class is not None and not isinstance(self.failure_class, FailureClass):
            object.__setattr__(self, "failure_class", FailureClass(self.failure_class))
        if not isinstance(self.action, RefinementAction):
            object.__setattr__(self, "action", RefinementAction(self.action))
        for name, value, minimum in (("attempt", self.attempt, 1), ("refinement_round", self.refinement_round, 0)):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ValueError(f"{name} must be an integer >= {minimum}")
        if self.next_binding_id is not None:
            object.__setattr__(self, "next_binding_id", validate_identifier(self.next_binding_id, "next_binding_id"))
        if self.next_reasoning_effort is not None:
            effort = validate_identifier(self.next_reasoning_effort, "next_reasoning_effort").lower()
            if effort not in _REASONING_ORDER:
                raise ValueError("next_reasoning_effort is unsupported")
            object.__setattr__(self, "next_reasoning_effort", effort)
        if self.next_tier is not None and not isinstance(self.next_tier, IntelligenceTier):
            object.__setattr__(self, "next_tier", IntelligenceTier(self.next_tier))
        _bounded_bool(self.requires_reconciliation, "requires_reconciliation")
        if isinstance(self.reasons, (str, bytes)) or not isinstance(self.reasons, Sequence) or len(self.reasons) > _MAX_REASONS:
            raise ValueError("reasons must contain at most 16 strings")
        reasons = tuple(validate_text(item, "reason", max_chars=256) for item in self.reasons)
        object.__setattr__(self, "reasons", tuple(dict.fromkeys(reasons)))
        if self.convergence is not None:
            if not isinstance(self.convergence, ConvergenceMetadata):
                raise ValueError("convergence must be ConvergenceMetadata or None")
            if self.convergence.refinement_round != self.refinement_round:
                raise ValueError("convergence refinement_round must match plan")
            expected_failure = self.failure_class.value if self.failure_class is not None else None
            if self.convergence.failure_class != expected_failure:
                raise ValueError("convergence failure_class must match plan")
        try:
            UUID(self.plan_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("plan_id must be a UUID string") from exc
        if self.action is RefinementAction.RECONCILE:
            if not self.requires_reconciliation or self.next_binding_id or self.next_reasoning_effort or self.next_tier:
                raise ValueError("reconcile plan must be reconciliation-only")
        elif self.requires_reconciliation:
            raise ValueError("only reconcile plan may require reconciliation")
        if self.action is RefinementAction.REASSIGN_SAME_TIER and self.next_binding_id is None:
            raise ValueError("same-tier reassignment requires next_binding_id")
        if self.action is RefinementAction.INCREASE_REASONING and self.next_reasoning_effort is None:
            raise ValueError("reasoning increase requires next_reasoning_effort")
        if self.action is RefinementAction.ESCALATE_TIER and self.next_tier is None:
            raise ValueError("tier escalation requires next_tier")
        if self.action not in {RefinementAction.REASSIGN_SAME_TIER} and self.next_binding_id is not None:
            raise ValueError("only same-tier reassignment may carry next_binding_id")
        if self.action not in {RefinementAction.INCREASE_REASONING} and self.next_reasoning_effort is not None:
            raise ValueError("only reasoning increase may carry next_reasoning_effort")
        if self.action not in {RefinementAction.ESCALATE_TIER} and self.next_tier is not None:
            raise ValueError("only tier escalation may carry next_tier")
        encoded = ensure_json_safe(self.to_dict(), "refinement plan")
        ensure_secret_free(encoded, "refinement plan")
        if len(json.dumps(encoded, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > _MAX_PLAN_BYTES:
            raise ValueError("refinement plan exceeds its size bound")

    def to_dict(self) -> dict[str, Any]:
        result = {
            "task_id": self.task_id,
            "failure_class": self.failure_class.value if self.failure_class is not None else None,
            "action": self.action.value,
            "attempt": self.attempt,
            "refinement_round": self.refinement_round,
            "next_binding_id": self.next_binding_id,
            "next_reasoning_effort": self.next_reasoning_effort,
            "next_tier": self.next_tier.value if self.next_tier is not None else None,
            "requires_reconciliation": self.requires_reconciliation,
            "reasons": list(self.reasons),
            "plan_id": self.plan_id,
        }
        if self.convergence is not None:
            result["convergence"] = self.convergence.to_dict()
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RefinementPlan":
        if not isinstance(value, Mapping):
            raise ValueError("refinement plan must be an object")
        reasons = value.get("reasons", ())
        if isinstance(reasons, (str, bytes)) or not isinstance(reasons, Sequence):
            raise ValueError("reasons must be a sequence")
        convergence_value = value.get("convergence")
        convergence = None
        if convergence_value is not None:
            convergence = ConvergenceMetadata.from_dict(convergence_value)
        return cls(
            task_id=value.get("task_id"),
            failure_class=value.get("failure_class"),
            action=value.get("action"),
            attempt=value.get("attempt"),
            refinement_round=value.get("refinement_round"),
            next_binding_id=value.get("next_binding_id"),
            next_reasoning_effort=value.get("next_reasoning_effort"),
            next_tier=value.get("next_tier"),
            requires_reconciliation=value.get("requires_reconciliation", False),
            reasons=tuple(reasons),
            convergence=convergence,
            plan_id=value.get("plan_id"),
        )


def _next_alternate_binding(context: RefinementContext) -> str | None:
    """Return the first Host-ranked candidate not already consumed.

    The caller owns candidate admission and must persist a subsequent context
    with consumed candidates removed.  This helper only makes the existing
    one-action policy pool-aware; it does not route, retry, or query providers.
    """

    if context.alternate_binding_ids:
        return context.alternate_binding_ids[0]
    return context.alternate_binding_id


class BoundedRefinementPolicy:
    """Select the least-expensive safe next action from Host observations."""

    def plan(self, context: RefinementContext) -> RefinementPlan:
        if not isinstance(context, RefinementContext):
            raise TypeError("context must be RefinementContext")

        failure = context.failure_class
        if failure is None:
            return self._plan(context, RefinementAction.NONE, "no_failure")

        # Safety outcomes precede all optimization choices.  An ambiguous
        # external effect is never exchanged for a fresh model call.
        if failure is FailureClass.UNKNOWN_EXTERNAL_EFFECT or not context.external_outcome_known:
            return self._plan(context, RefinementAction.RECONCILE, "unknown_external_effect", requires_reconciliation=True)
        if failure is FailureClass.SECURITY_EGRESS_AUTHORITY:
            return self._plan(context, RefinementAction.HUMAN, "security_egress_authority")
        if context.now_epoch >= context.deadline_epoch:
            return self._plan(context, RefinementAction.FAIL, "deadline_exceeded")
        if context.attempt >= context.max_attempts:
            return self._plan(context, RefinementAction.FAIL, "attempt_ceiling_reached")
        if context.estimated_cost > context.budget_remaining:
            return self._plan(context, RefinementAction.HUMAN, "budget_insufficient")

        if failure is FailureClass.FORMAT_PATCH:
            if context.refinement_round < context.max_refinement_rounds and context.correction_available:
                return self._plan(context, RefinementAction.CORRECT, "format_failure_correction", round_increment=True)
            return self._bounded_fallback(context, "format_refinement_exhausted")

        if failure is FailureClass.SEMANTIC_TEST:
            if context.refinement_round < context.max_refinement_rounds:
                if context.critic_available:
                    return self._plan(context, RefinementAction.CRITIQUE, "semantic_test_critic", round_increment=True)
                if context.correction_available:
                    return self._plan(context, RefinementAction.CORRECT, "semantic_test_correction", round_increment=True)
            return self._bounded_fallback(context, "semantic_refinement_exhausted")

        if failure is FailureClass.PROVIDER_TRANSPORT:
            next_binding = _next_alternate_binding(context)
            if next_binding is not None and not context.model_change_used:
                return self._plan(
                    context,
                    RefinementAction.REASSIGN_SAME_TIER,
                    "provider_transport_reassign",
                    next_binding_id=next_binding,
                )
            return self._plan(context, RefinementAction.FAIL, "provider_pool_exhausted")

        if failure is FailureClass.CAPABILITY_REASONING:
            next_binding = _next_alternate_binding(context)
            if next_binding is not None and not context.model_change_used:
                return self._plan(
                    context,
                    RefinementAction.REASSIGN_SAME_TIER,
                    "capability_model_change",
                    next_binding_id=next_binding,
                )
            next_effort = self._next_reasoning_effort(context)
            if next_effort is not None and not context.reasoning_escalated:
                return self._plan(
                    context,
                    RefinementAction.INCREASE_REASONING,
                    "capability_increase_reasoning",
                    next_reasoning_effort=next_effort,
                )
            next_tier = self._next_tier(context)
            if next_tier is not None and context.escalation_count < context.max_escalations:
                return self._plan(
                    context,
                    RefinementAction.ESCALATE_TIER,
                    "capability_escalate_tier",
                    next_tier=next_tier,
                )
            return self._plan(context, RefinementAction.HUMAN, "capability_escalation_exhausted")

        return self._plan(context, RefinementAction.HUMAN, "unhandled_failure")

    def _bounded_fallback(self, context: RefinementContext, reason: str) -> RefinementPlan:
        next_binding = _next_alternate_binding(context)
        if next_binding is not None and not context.model_change_used:
            return self._plan(
                context,
                RefinementAction.REASSIGN_SAME_TIER,
                reason,
                next_binding_id=next_binding,
            )
        return self._plan(context, RefinementAction.HUMAN, reason)

    @staticmethod
    def _next_reasoning_effort(context: RefinementContext) -> str | None:
        current = _REASONING_ORDER.index(context.current_reasoning_effort)
        for effort in context.allowed_reasoning_efforts:
            position = _REASONING_ORDER.index(effort)
            if position <= current:
                continue
            if effort == "high" and not context.allow_high_reasoning:
                continue
            if position > _REASONING_ORDER.index(_AUTOMATIC_REASONING_CEILING) and not context.allow_high_reasoning:
                continue
            return effort
        return None

    @staticmethod
    def _next_tier(context: RefinementContext) -> IntelligenceTier | None:
        current = _TIER_ORDER.index(context.current_tier)
        for tier in context.allowed_tiers:
            if _TIER_ORDER.index(tier) > current:
                return tier
        return None

    @staticmethod
    def _plan(
        context: RefinementContext,
        action: RefinementAction,
        reason: str,
        *,
        round_increment: bool = False,
        next_binding_id: str | None = None,
        next_reasoning_effort: str | None = None,
        next_tier: IntelligenceTier | None = None,
        requires_reconciliation: bool = False,
    ) -> RefinementPlan:
        return RefinementPlan(
            task_id=context.task_id,
            failure_class=context.failure_class,
            action=action,
            attempt=context.attempt,
            refinement_round=context.refinement_round + (1 if round_increment else 0),
            next_binding_id=next_binding_id,
            next_reasoning_effort=next_reasoning_effort,
            next_tier=next_tier,
            requires_reconciliation=requires_reconciliation,
            reasons=(reason,),
        )


__all__ = [
    "BoundedRefinementPolicy",
    "ConvergenceMetadata",
    "CriticFinding",
    "FailureClass",
    "RefinementAction",
    "RefinementContext",
    "RefinementPlan",
    "RefinementProposal",
]
