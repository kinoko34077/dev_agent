"""Host composition for bounded Worker-failure criticism.

This module adapts the existing Supervisor ReviewPacket into the compact,
reference-first packet accepted by the proposal-only L1 Critic.  It does not
select resources, persist a refinement decision, reassign a task, or integrate
source; those authorities remain with the existing Host/Commander boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

from src.dev_agent.intelligence.critic_adapter import ModelCriticAdapter
from src.dev_agent.providers.host_dispatch import route_through_host
from src.dev_agent.intelligence.refinement import (
    BoundedRefinementPolicy,
    RefinementAction,
    FailureClass,
    RefinementContext,
    RefinementProposal,
    RefinementPlan,
)


class RefinementCompositionError(ValueError):
    """The public Supervisor packet cannot safely become a Critic packet."""


@dataclass(frozen=True)
class RefinementActionResult:
    """Bounded result of applying one Host-selected refinement action.

    This projection intentionally omits the returned handoff and provider
    result.  Those artifacts remain in their existing durable boundaries; the
    adapter only reports whether one existing Host operation was invoked.
    """

    plan_id: str
    task_id: str
    action: RefinementAction
    status: str
    executed: bool
    handoff_created: bool = False
    thinking_escalated: bool = False
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "action": self.action.value,
            "status": self.status,
            "executed": self.executed,
            "handoff_created": self.handoff_created,
            "thinking_escalated": self.thinking_escalated,
            "reason": self.reason,
        }


class ReviewPacketSource(Protocol):
    """Minimal public boundary required from the Supervisor composition."""

    def review_packet(self, task_id: str) -> Mapping[str, Any]:
        ...


_FORMAT_FAILURES = frozenset(
    {
        "format_patch",
        "patch_format_failure",
        "malformed_patch",
        "hunk_apply_failure",
        "delimiter_imbalance",
        "json_shape_failure",
        "syntax_incomplete",
        "new_file_contract_failure",
        "model_output_invalid",
    }
)
_SEMANTIC_FAILURES = frozenset(
    {
        "semantic_test",
        "test_failure",
        "host_verification_failure",
    }
)
_PROVIDER_FAILURES = frozenset(
    {
        "provider_failure",
        "provider_error",
        "provider_transport",
        "transport_failure",
        "provider_unavailable",
        "rate_limit",
        "quota",
        "quota_exhausted",
        "authentication_failure",
        "authorization_failure",
        "binding_saturation",
        "timeout",
    }
)
_CAPABILITY_FAILURES = frozenset(
    {
        "capability_failure",
        "capability_reasoning",
        "reasoning_failure",
    }
)
_SECURITY_FAILURES = frozenset(
    {
        "security_egress_authority",
        "manifest_input_failure",
        "scope_violation",
        "egress_denied",
        "secret_detected",
        "protected_path",
        "approval_missing",
    }
)
_UNKNOWN_FAILURES = frozenset(
    {
        "unknown",
        "unknown_external_effect",
        "timeout_after_send",
        "connection_lost_after_send",
        "decode_ambiguity",
        "billing_reconciliation_unknown",
        "lease_loss_after_external_effect",
    }
)


def classify_worker_failure(
    failure_category: str,
    *,
    external_outcome_known: bool = True,
) -> FailureClass:
    """Map one Host category to a bounded refinement failure class.

    The mapping is intentionally closed rather than substring-based.  An
    unknown category must be classified by the Host before any model change
    or retry is considered.  ``external_outcome_known=False`` always wins and
    prevents a fresh dispatch from being selected for an ambiguous effect.
    """

    if not isinstance(failure_category, str) or not failure_category.strip():
        raise RefinementCompositionError("failure_category must be non-empty text")
    if not isinstance(external_outcome_known, bool):
        raise RefinementCompositionError("external_outcome_known must be a boolean")
    category = failure_category.strip().lower().replace("-", "_").replace(" ", "_")
    if not external_outcome_known or category in _UNKNOWN_FAILURES:
        return FailureClass.UNKNOWN_EXTERNAL_EFFECT
    if category in _SECURITY_FAILURES:
        return FailureClass.SECURITY_EGRESS_AUTHORITY
    if category in _FORMAT_FAILURES:
        return FailureClass.FORMAT_PATCH
    if category in _SEMANTIC_FAILURES:
        return FailureClass.SEMANTIC_TEST
    if category in _CAPABILITY_FAILURES:
        return FailureClass.CAPABILITY_REASONING
    if category in _PROVIDER_FAILURES:
        return FailureClass.PROVIDER_TRANSPORT
    raise RefinementCompositionError(f"unsupported failure category: {category}")


def plan_refinement(
    context: RefinementContext,
    failure_category: str,
) -> RefinementPlan:
    """Plan one bounded next action from a Host failure category.

    Failure classification is the only adaptation performed here.  The
    existing ``BoundedRefinementPolicy`` remains the single owner of the next
    action, while dispatch, reassignment, persistence, approval, and
    reconciliation stay with their existing Host boundaries.
    """

    if not isinstance(context, RefinementContext):
        raise RefinementCompositionError("context must be RefinementContext")
    failure = classify_worker_failure(
        failure_category,
        external_outcome_known=context.external_outcome_known,
    )
    return BoundedRefinementPolicy().plan(replace(context, failure_class=failure))


def _failure_class(value: FailureClass | str) -> str:
    try:
        return FailureClass(value).value
    except (TypeError, ValueError) as exc:
        raise RefinementCompositionError("failure_class is unsupported") from exc


def _bounded_summary(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RefinementCompositionError("failure_summary must be non-empty text")
    normalized = value.strip()
    if len(normalized) > 4_000:
        raise RefinementCompositionError("failure_summary exceeds the input limit")
    return normalized


def build_refinement_packet(
    runner: ReviewPacketSource,
    task_id: str,
    *,
    failure_class: FailureClass | str,
    failure_summary: str,
) -> dict[str, Any]:
    """Build one bounded Critic packet through the public Supervisor API.

    Only the fields needed for correction criticism are copied.  In
    particular, verification summaries and any accidental raw fields in a
    caller-provided packet are not forwarded; artifact references and the
    verified patch digest remain the only evidence handles.
    """

    if not callable(getattr(runner, "review_packet", None)):
        raise RefinementCompositionError("runner must expose review_packet(task_id)")
    if not isinstance(task_id, str) or not task_id.strip():
        raise RefinementCompositionError("task_id must be non-empty text")
    try:
        review_packet = runner.review_packet(task_id)
    except Exception as exc:
        raise RefinementCompositionError("Supervisor review packet is unavailable") from exc
    if not isinstance(review_packet, Mapping):
        raise RefinementCompositionError("Supervisor review packet must be an object")
    packet_task_id = review_packet.get("task_id")
    if packet_task_id != task_id:
        raise RefinementCompositionError("Supervisor review packet task_id does not match task_id")
    attempt_id = review_packet.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise RefinementCompositionError("Supervisor review packet has no attempt_id")

    artifact_refs = review_packet.get("artifact_refs", [])
    if not isinstance(artifact_refs, list):
        raise RefinementCompositionError("Supervisor review packet artifact_refs must be a list")

    result: dict[str, Any] = {
        "task_id": task_id,
        "attempt_id": attempt_id,
        "failure_class": _failure_class(failure_class),
        "failure_summary": _bounded_summary(failure_summary),
        "changed_files": list(review_packet.get("changed_files", [])),
        "evidence_refs": [dict(item) if isinstance(item, Mapping) else item for item in artifact_refs],
        "acceptance": list(review_packet.get("acceptance", [])),
    }
    patch_sha256 = review_packet.get("patch_sha256")
    if patch_sha256 is not None:
        result["patch_sha256"] = patch_sha256
    return result


def propose_critic(
    runner: ReviewPacketSource,
    provider: Any,
    task_id: str,
    *,
    failure_class: FailureClass | str,
    failure_summary: str,
    max_output_tokens: int = 512,
    execution_boundary: str = "in_process",
    host_executor: Any | None = None,
) -> RefinementProposal:
    """Request one proposal-only L1 Critic result from a Host-selected provider."""

    packet = build_refinement_packet(
        runner,
        task_id,
        failure_class=failure_class,
        failure_summary=failure_summary,
    )
    if execution_boundary not in {"in_process", "host_process"}:
        raise RefinementCompositionError("execution_boundary must be in_process or host_process")
    critic_provider = provider
    if execution_boundary == "host_process":
        if not callable(host_executor):
            raise RefinementCompositionError("host_process critic requires host_executor")
        critic_provider = route_through_host(provider, host_executor)
    return ModelCriticAdapter(critic_provider, max_output_tokens=max_output_tokens).propose(packet)


def _assignment_values(
    plan: RefinementPlan,
    assignment: Mapping[str, Any] | None,
) -> tuple[str, str, str | None]:
    if not isinstance(assignment, Mapping):
        raise RefinementCompositionError("assignment is required for a Worker refinement action")
    values: list[str | None] = []
    for name, maximum in (("provider_id", 128), ("model_id", 256)):
        value = assignment.get(name)
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
            raise RefinementCompositionError(f"assignment.{name} must be bounded non-empty text")
        values.append(value.strip())
    binding = assignment.get("provider_binding_id")
    if binding is not None:
        if not isinstance(binding, str) or not binding.strip() or len(binding.strip()) > 256:
            raise RefinementCompositionError("assignment.provider_binding_id must be bounded text")
        binding = binding.strip()
    if plan.action is RefinementAction.REASSIGN_SAME_TIER:
        if plan.next_binding_id is None:
            raise RefinementCompositionError("same-tier plan has no next binding")
        if binding is not None and binding != plan.next_binding_id:
            raise RefinementCompositionError("assignment binding does not match refinement plan")
        binding = plan.next_binding_id
    return values[0], values[1], binding


def _critic_correction(
    plan: RefinementPlan,
    proposal: RefinementProposal | None,
) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(proposal, RefinementProposal):
        raise RefinementCompositionError("CRITIQUE action requires a RefinementProposal")
    if proposal.task_id != plan.task_id:
        raise RefinementCompositionError("critic proposal task_id does not match refinement plan")
    if not proposal.findings:
        raise RefinementCompositionError("critic proposal must contain a finding")
    correction = "\n".join(
        f"{finding.location}: {finding.required_correction}" for finding in proposal.findings
    )
    return _bounded_summary(correction), {
        "kind": "refinement_proposal",
        "task_id": proposal.task_id,
        "attempt_id": proposal.attempt_id,
        "evidence_refs": list(proposal.evidence_refs),
    }


def apply_refinement_action(
    runner: Any,
    plan: RefinementPlan,
    *,
    failure_evidence_reference: Mapping[str, Any] | Any | None = None,
    review_findings_reference: Mapping[str, Any] | Any | None = None,
    required_correction: str | None = None,
    critic_proposal: RefinementProposal | None = None,
    assignment: Mapping[str, Any] | None = None,
) -> RefinementActionResult:
    """Execute at most one existing Host action for a refinement plan.

    The policy remains the owner of action selection.  This adapter only
    turns a correction/critique/same-tier plan into one existing Supervisor
    ``rework_handoff`` plus ``reassign`` call.  It never loops, selects a
    resource, changes ownership, consumes approval, or integrates a patch.
    Non-dispatch actions are returned as explicit bounded statuses for their
    owning reconciliation or authority boundary.
    """

    if not isinstance(plan, RefinementPlan):
        raise RefinementCompositionError("plan must be RefinementPlan")
    if not callable(getattr(runner, "reassign", None)):
        raise RefinementCompositionError("runner must expose reassign()")
    action = plan.action
    if action in {RefinementAction.CORRECT, RefinementAction.CRITIQUE, RefinementAction.REASSIGN_SAME_TIER}:
        provider_id, model_id, binding_id = _assignment_values(plan, assignment)
    else:
        provider_id = model_id = binding_id = None

    if action is RefinementAction.CORRECT:
        if not isinstance(failure_evidence_reference, Mapping) or not failure_evidence_reference:
            raise RefinementCompositionError("CORRECT action requires failure evidence reference")
        correction = _bounded_summary(required_correction or "")
        if not callable(getattr(runner, "rework_handoff", None)):
            raise RefinementCompositionError("runner must expose rework_handoff()")
        handoff = runner.rework_handoff(
            plan.task_id,
            failure_evidence_reference=failure_evidence_reference,
            review_findings_reference=review_findings_reference,
            required_correction=correction,
        )
        runner.reassign(
            plan.task_id,
            provider_id=provider_id,
            model_id=model_id,
            provider_binding_id=binding_id,
            rework_handoff=handoff,
        )
        return RefinementActionResult(
            plan_id=plan.plan_id,
            task_id=plan.task_id,
            action=action,
            status="REWORK_DISPATCHED",
            executed=True,
            handoff_created=True,
            reason=plan.reasons[0] if plan.reasons else None,
        )

    if action is RefinementAction.CRITIQUE:
        if not isinstance(failure_evidence_reference, Mapping) or not failure_evidence_reference:
            raise RefinementCompositionError("CRITIQUE action requires failure evidence reference")
        correction, critic_reference = _critic_correction(plan, critic_proposal)
        if not callable(getattr(runner, "rework_handoff", None)):
            raise RefinementCompositionError("runner must expose rework_handoff()")
        handoff = runner.rework_handoff(
            plan.task_id,
            failure_evidence_reference=failure_evidence_reference,
            review_findings_reference=review_findings_reference or critic_reference,
            required_correction=correction,
        )
        runner.reassign(
            plan.task_id,
            provider_id=provider_id,
            model_id=model_id,
            provider_binding_id=binding_id,
            rework_handoff=handoff,
        )
        return RefinementActionResult(
            plan_id=plan.plan_id,
            task_id=plan.task_id,
            action=action,
            status="REWORK_DISPATCHED",
            executed=True,
            handoff_created=True,
            reason=plan.reasons[0] if plan.reasons else None,
        )

    if action is RefinementAction.REASSIGN_SAME_TIER:
        runner.reassign(
            plan.task_id,
            provider_id=provider_id,
            model_id=model_id,
            provider_binding_id=binding_id,
        )
        return RefinementActionResult(
            plan_id=plan.plan_id,
            task_id=plan.task_id,
            action=action,
            status="REASSIGNED",
            executed=True,
            reason=plan.reasons[0] if plan.reasons else None,
        )

    status_by_action = {
        RefinementAction.NONE: "NO_ACTION",
        RefinementAction.RECONCILE: "RECONCILIATION_REQUIRED",
        RefinementAction.HUMAN: "HUMAN_REQUIRED",
        RefinementAction.FAIL: "FAILED",
        RefinementAction.INCREASE_REASONING: "HOST_ACTION_REQUIRED",
        RefinementAction.ESCALATE_TIER: "HOST_ACTION_REQUIRED",
    }
    return RefinementActionResult(
        plan_id=plan.plan_id,
        task_id=plan.task_id,
        action=action,
        status=status_by_action[action],
        executed=False,
        thinking_escalated=action is RefinementAction.INCREASE_REASONING,
        reason=plan.reasons[0] if plan.reasons else None,
    )


__all__ = [
    "RefinementActionResult",
    "RefinementCompositionError",
    "apply_refinement_action",
    "ReviewPacketSource",
    "build_refinement_packet",
    "classify_worker_failure",
    "plan_refinement",
    "propose_critic",
]
