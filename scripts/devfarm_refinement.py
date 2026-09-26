"""Host composition for bounded Worker-failure criticism.

This module adapts the existing Supervisor ReviewPacket into the compact,
reference-first packet accepted by the proposal-only L1 Critic.  It does not
select resources, persist a refinement decision, reassign a task, or integrate
source; those authorities remain with the existing Host/Commander boundaries.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from src.dev_agent.intelligence.critic_adapter import ModelCriticAdapter
from src.dev_agent.providers.host_dispatch import route_through_host
from src.dev_agent.coordination.protocol_helpers import ensure_json_safe, ensure_secret_free
from scripts.devfarm_review_protocol import normalize_review_decision
from src.dev_agent.intelligence.refinement import (
    BoundedRefinementPolicy,
    RefinementAction,
    FailureClass,
    RefinementContext,
    RefinementProposal,
    RefinementPlan,
)
from src.dev_agent.intelligence.convergence import (
    ConcreteFailureSpec,
    ConvergenceMetadata,
    FailureFingerprint,
    RepairDirective,
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
        "contract_shape_regression",
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
    *,
    source_attempt_id: str | None = None,
    current_model_identity: str = "host-policy",
    previous_model_identity: str | None = None,
    validator_refs: tuple[str, ...] = (),
    test_ids: tuple[str, ...] = (),
    response_contract: str | None = None,
    patch_category: str | None = None,
    error_code: str | None = None,
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
    plan = BoundedRefinementPolicy().plan(replace(context, failure_class=failure))
    # FORMAT/PATCH and SEMANTIC/TEST are the model-convergence lane.  External
    # effects, authority, and provider failures remain on their existing
    # reconciliation/failover boundaries and must not be represented as model
    # refinement work.
    if failure not in {FailureClass.FORMAT_PATCH, FailureClass.SEMANTIC_TEST}:
        return plan

    default_validator = (
        "worker:patch_validation"
        if failure is FailureClass.FORMAT_PATCH
        else "worker:host_verification"
    )
    fingerprint = FailureFingerprint.from_observation(
        failure_class=failure.value,
        validator_refs=validator_refs or (default_validator,),
        response_contract=response_contract,
        test_ids=test_ids,
        patch_category=patch_category or (
            "patch_output" if failure is FailureClass.FORMAT_PATCH else "semantic_contract"
        ),
        error_code=error_code or (
            "PATCH_VALIDATION_FAILED"
            if failure is FailureClass.FORMAT_PATCH
            else "HOST_VERIFICATION_FAILED"
        ),
    )
    attempt_id = source_attempt_id or f"attempt-{context.attempt}-{context.task_id}"
    convergence = ConvergenceMetadata.from_validation(
        passed=False,
        refinement_round=plan.refinement_round,
        current_model_identity=current_model_identity,
        source_attempt_id=attempt_id,
        failure=fingerprint,
        correction_actor="host_policy",
        previous_model_identity=previous_model_identity,
        validator_refs=fingerprint.validator_refs,
    )
    return replace(plan, convergence=convergence)


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


_REPAIR_FAILURE_CLASSES = frozenset({FailureClass.FORMAT_PATCH, FailureClass.SEMANTIC_TEST})


def _repair_context_for_handoff(
    plan: RefinementPlan,
    *,
    failure_evidence_reference: Mapping[str, Any] | Any | None,
    failure_spec: ConcreteFailureSpec | Mapping[str, Any] | None,
    repair_directive: RepairDirective | Mapping[str, Any] | None,
    source_attempt_id: str | None = None,
    unresolved_constraints: Sequence[str] = (),
    resolved_constraints: Sequence[str] = (),
) -> tuple[ConcreteFailureSpec, RepairDirective, dict[str, Any]] | None:
    """Bind the latest model failure to a fresh rework handoff.

    Provider/runtime failures deliberately stay outside this path.  The
    returned context is bounded audit data, not authority to mutate source.
    """

    supplied = failure_spec is not None or repair_directive is not None
    if not supplied:
        return None
    if plan.failure_class not in _REPAIR_FAILURE_CLASSES:
        raise RefinementCompositionError(
            "repair state is only valid for FORMAT_PATCH or SEMANTIC_TEST"
        )
    if not isinstance(failure_evidence_reference, Mapping) or not failure_evidence_reference:
        raise RefinementCompositionError("repair state requires failure evidence reference")
    if failure_spec is None:
        raise RefinementCompositionError("repair_directive requires failure_spec")
    spec = failure_spec if isinstance(failure_spec, ConcreteFailureSpec) else ConcreteFailureSpec.from_dict(failure_spec)
    directive = (
        repair_directive
        if isinstance(repair_directive, RepairDirective)
        else RepairDirective.from_dict(repair_directive)
        if repair_directive is not None
        else RepairDirective.from_failure_spec(spec)
    )
    if source_attempt_id is None and isinstance(failure_evidence_reference, Mapping):
        candidate = failure_evidence_reference.get("attempt_id")
        if isinstance(candidate, str) and candidate.strip():
            source_attempt_id = candidate.strip()
    def bounded_constraints(value: Sequence[str], name: str) -> list[str]:
        if isinstance(value, (str, bytes)):
            raise RefinementCompositionError(f"{name} must be a sequence")
        items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        if len(items) > 32 or any(len(item) > 512 for item in items):
            raise RefinementCompositionError(f"{name} is too large")
        return list(dict.fromkeys(items))

    unresolved = bounded_constraints(unresolved_constraints, "unresolved_constraints")
    resolved = bounded_constraints(resolved_constraints, "resolved_constraints")
    context = {
        "source_attempt_id": source_attempt_id,
        "source_failure_signature": spec.failure_signature,
        "latest_failure_spec": spec.to_dict(),
        "latest_repair_directive": directive.to_dict(),
        "repair_directive_hash": directive.directive_hash,
        "current_repair": directive.to_dict(),
        "historical_constraints": {
            "unresolved": unresolved,
            "resolved_must_not_regress": resolved,
        },
        "directive_rebound": True,
    }
    ensure_json_safe(context, "repair handoff context")
    ensure_secret_free(context, "repair handoff context")
    return spec, directive, context


def build_concrete_failure_spec_from_error(
    error: Mapping[str, Any],
    *,
    validator_ref: str = "worker:output_contract",
) -> ConcreteFailureSpec:
    """Build a FailureSpec from a bounded structured Host validator error.

    This is an additive input path.  Existing string-based validators remain
    compatible, while new validators can avoid substring matching and retain
    exact bounded location/observed/expected facts.
    """

    if not isinstance(error, Mapping):
        raise RefinementCompositionError("structured validator error must be an object")
    allowed = {
        "error_code", "failure_class", "stage", "location", "observed", "expected",
        "problem", "required_correction", "must_preserve", "forbidden_changes",
        "acceptance_checks", "validator_ref",
    }
    unknown = set(error) - allowed
    if unknown:
        raise RefinementCompositionError(f"structured validator error has unknown field: {sorted(unknown)[0]}")
    error_code = error.get("error_code")
    if not isinstance(error_code, str) or not error_code.strip():
        raise RefinementCompositionError("structured validator error requires error_code")
    selected_validator = error.get("validator_ref", validator_ref)
    if not isinstance(selected_validator, str) or not selected_validator.strip():
        raise RefinementCompositionError("structured validator error requires validator_ref")
    try:
        return ConcreteFailureSpec(
            failure_class=error.get("failure_class", "FORMAT_PATCH"),
            stage=error.get("stage", "worker_output_validation"),
            location=error.get("location", "worker_output"),
            observed=error.get("observed", "deterministic validator rejection"),
            expected=error.get("expected", "bounded Worker output"),
            problem=error.get("problem", "the Worker output failed deterministic validation"),
            required_correction=error.get("required_correction"),
            must_preserve=tuple(error.get("must_preserve", ())),
            forbidden_changes=tuple(error.get("forbidden_changes", ())),
            acceptance_checks=tuple(error.get("acceptance_checks", ())),
            validator_refs=(selected_validator, error_code),
        )
    except (TypeError, ValueError) as exc:
        raise RefinementCompositionError("structured validator error is not bounded") from exc


def build_concrete_failure_spec(
    reason: str | Mapping[str, Any],
    *,
    manifest: Mapping[str, Any] | None = None,
    validator_ref: str = "worker:output_contract",
) -> ConcreteFailureSpec:
    """Convert known deterministic Worker findings into safe repair facts.

    The input reason is a Host validator detail and is deliberately not copied
    into the resulting artifact.  Unknown details receive a generic bounded
    projection rather than leaking raw model output into the next attempt.
    """

    if isinstance(reason, Mapping):
        return build_concrete_failure_spec_from_error(reason, validator_ref=validator_ref)
    if not isinstance(reason, str) or not reason.strip():
        raise RefinementCompositionError("failure reason must be non-empty text")
    normalized = reason.casefold()
    allowed_files = tuple(
        item.strip()
        for item in ((manifest or {}).get("allowed_files", ()) if isinstance(manifest, Mapping) else ())
        if isinstance(item, str) and item.strip()
    )
    preserve = ("task objective", "supplied file scope")
    forbidden = ("unrelated files", "new output fields")
    if "unsupported fields" in normalized or "additional field" in normalized:
        spec = ConcreteFailureSpec(
            failure_class="FORMAT_PATCH",
            stage="worker_output_validation",
            location="worker_output",
            observed="output contains fields outside the minimal Worker contract",
            expected="only file_replacements and optional notes",
            problem="the minimal Worker output contains unsupported fields",
            required_correction=(
                "Return exactly one JSON object with file_replacements and, optionally, notes. "
                "Remove every other top-level field, including output_contract."
            ),
            must_preserve=preserve,
            forbidden_changes=("adding metadata fields", "changing file scope"),
            acceptance_checks=(
                "top-level keys are file_replacements and optional notes",
                "file_replacements contains only supplied outbound paths",
            ),
            validator_refs=(validator_ref,),
        )
    elif (
        "worker response json is invalid" in normalized
        or "invalid json" in normalized
        or "did not contain a json object" in normalized
        or "does not contain a json object" in normalized
        or "must contain a json object" in normalized
    ):
        spec = ConcreteFailureSpec(
            failure_class="FORMAT_PATCH",
            stage="worker_output_validation",
            location="worker_output",
            observed="invalid JSON object",
            expected="one JSON object matching the Host Worker response schema",
            problem="the Worker response could not be decoded as one JSON object",
            required_correction="Return exactly one valid JSON object with no Markdown or prose. Escape every quote, backslash, and newline inside JSON strings; do not emit trailing commas or raw line breaks inside strings.",
            must_preserve=preserve,
            forbidden_changes=("Markdown fences", "prose outside the JSON object", "raw newlines inside JSON strings", "unrelated files"),
            acceptance_checks=("response parses as one JSON object", "response satisfies the Host Worker schema", "file_replacements contains only supplied paths"),
            validator_refs=(validator_ref,),
        )
    elif "known_issues" in normalized and ("list" in normalized or "array" in normalized):
        spec = ConcreteFailureSpec(
            failure_class="FORMAT_PATCH",
            stage="worker_output_validation",
            location="known_issues",
            observed="scalar",
            expected="array<string>",
            problem="known_issues must be a JSON array",
            required_correction="Return known_issues as a JSON array; use [] when empty.",
            must_preserve=preserve,
            forbidden_changes=forbidden,
            acceptance_checks=("known_issues is an array", "every known issue is a string"),
            validator_refs=(validator_ref,),
        )
    elif "assumptions" in normalized and ("list" in normalized or "array" in normalized):
        spec = ConcreteFailureSpec(
            failure_class="FORMAT_PATCH",
            stage="worker_output_validation",
            location="assumptions",
            observed="scalar",
            expected="array<string>",
            problem="assumptions must be a JSON array",
            required_correction="Return assumptions as a JSON array; use [] when there are no assumptions.",
            must_preserve=preserve,
            forbidden_changes=forbidden,
            acceptance_checks=("assumptions is an array", "every assumption is a string"),
            validator_refs=(validator_ref,),
        )
    elif "file replacement lines must be strings" in normalized:
        spec = ConcreteFailureSpec(
            failure_class="FORMAT_PATCH",
            stage="worker_output_validation",
            location="file_replacements",
            observed="replacement line is not a string",
            expected="array<string> with one complete source line per item",
            problem="every file replacement line must be a JSON string",
            required_correction="Return file_replacements as an object whose value is an array of complete source-line strings; do not emit objects, numbers, or null items.",
            must_preserve=preserve,
            forbidden_changes=("using non-string line items", "switching to a unified diff", "changing unrelated content"),
            acceptance_checks=("every replacement line is a string", "each replacement line contains no LF or CR"),
            validator_refs=(validator_ref,),
        )
    elif "outside manifest allowed_files" in normalized or "outside manifest allowed files" in normalized:
        allowed = ", ".join(allowed_files) if allowed_files else "the supplied allowed_files"
        spec = ConcreteFailureSpec(
            failure_class="FORMAT_PATCH",
            stage="worker_output_validation",
            location="file_replacements",
            observed="path outside allowed_files",
            expected="one supplied outbound file path",
            problem="file_replacements contains a path outside the manifest scope",
            required_correction=f"Use exactly one supplied outbound path from: {allowed}.",
            must_preserve=preserve,
            forbidden_changes=("adding a new path", "changing ownership scope"),
            acceptance_checks=("every replacement key is in allowed_files", "every replacement key is in outbound_files"),
            validator_refs=(validator_ref,),
        )
    elif "must not contain newlines" in normalized or "embedded newline" in normalized:
        spec = ConcreteFailureSpec(
            failure_class="FORMAT_PATCH",
            stage="worker_output_validation",
            location="file_replacements",
            observed="line-array element contains a newline",
            expected="one source line per array element",
            problem="a replacement line contains an embedded newline",
            required_correction="Each array element must represent one source line; split multiline content and ensure no element contains LF or CR.",
            must_preserve=preserve,
            forbidden_changes=("switching to a unified diff", "changing unrelated content"),
            acceptance_checks=("each replacement line contains no LF or CR",),
            validator_refs=(validator_ref,),
        )
    elif "file_replacements must be an object" in normalized:
        spec = ConcreteFailureSpec(
            failure_class="FORMAT_PATCH",
            stage="worker_output_validation",
            location="file_replacements",
            observed="non-object",
            expected="object mapping one supplied path to complete content",
            problem="file_replacements must be a JSON object",
            required_correction="Return file_replacements as an object keyed only by the supplied file path.",
            must_preserve=preserve,
            forbidden_changes=("using an array as the top-level replacement",),
            acceptance_checks=("file_replacements is an object",),
            validator_refs=(validator_ref,),
        )
    else:
        spec = ConcreteFailureSpec(
            failure_class="FORMAT_PATCH",
            stage="worker_output_validation",
            location="worker_output",
            observed="deterministic validator rejection",
            expected="bounded Worker result satisfying the output contract",
            problem="the Worker output failed deterministic validation",
            required_correction="Return a fresh JSON result satisfying every listed output-contract requirement.",
            must_preserve=preserve,
            forbidden_changes=forbidden,
            acceptance_checks=("output passes the Host Worker validator",),
            validator_refs=(validator_ref,),
        )
    return spec


def build_refinement_packet(
    runner: ReviewPacketSource,
    task_id: str,
    *,
    failure_class: FailureClass | str,
    failure_summary: str,
    failure_spec: ConcreteFailureSpec | Mapping[str, Any] | None = None,
    repair_directive: RepairDirective | Mapping[str, Any] | None = None,
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
    spec_value = failure_spec if failure_spec is not None else review_packet.get("failure_spec")
    if spec_value is not None:
        spec = spec_value if isinstance(spec_value, ConcreteFailureSpec) else ConcreteFailureSpec.from_dict(spec_value)
        result["failure_spec"] = spec.to_dict()
        directive_value = repair_directive if repair_directive is not None else review_packet.get("repair_directive")
        directive = (
            RepairDirective.from_failure_spec(spec)
            if directive_value is None
            else directive_value if isinstance(directive_value, RepairDirective)
            else RepairDirective.from_dict(directive_value)
        )
        result["repair_directive"] = directive.to_dict()
    elif repair_directive is not None:
        raise RefinementCompositionError("repair_directive requires failure_spec")
    return result


def build_reviewer_rework_packet(
    runner: ReviewPacketSource,
    task_id: str,
    review_decision: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Project one durable Reviewer decision into a bounded rework packet.

    ``APPROVE_INTEGRATION`` is a fast-path projection and returns ``None``;
    this helper never creates an integration decision.  Only ``REWORK`` is
    converted, and the packet carries the durable decision identity and
    evidence references rather than copying reviewer prose into a Worker
    request.  The caller still has to pass the packet through the existing
    ``RefinementPlan`` and ``apply_refinement_action`` Host boundaries.
    """

    try:
        normalized = normalize_review_decision(review_decision)
        ensure_json_safe(normalized, "review decision")
        ensure_secret_free(normalized, "review decision")
    except (TypeError, ValueError) as exc:
        raise RefinementCompositionError("review decision is not a safe bounded object") from exc

    decision = normalized["decision"]
    if decision == "APPROVE_INTEGRATION":
        return None
    if decision != "REWORK":
        raise RefinementCompositionError("only a durable REWORK decision can create a rework packet")

    if normalized["task_id"] != task_id:
        raise RefinementCompositionError("review decision task_id does not match task_id")
    packet = build_refinement_packet(
        runner,
        task_id,
        failure_class=FailureClass.SEMANTIC_TEST,
        failure_summary="independent Reviewer requested bounded rework",
    )
    if packet["attempt_id"] != normalized["attempt_id"]:
        raise RefinementCompositionError("review decision attempt_id does not match the current attempt")
    required_correction = normalized.get("required_correction")
    if not isinstance(required_correction, str) or not required_correction.strip():
        raise RefinementCompositionError("REWORK decision requires required_correction")
    required_correction = _bounded_summary(required_correction)
    ensure_secret_free({"required_correction": required_correction}, "review correction")
    packet["required_correction"] = required_correction
    packet["review_findings_reference"] = {
        "kind": "review_findings",
        "decision_id": normalized["decision_id"],
        "task_id": normalized["task_id"],
        "attempt_id": normalized["attempt_id"],
        "finding_count": len(normalized["findings"]),
        "evidence_refs": list(normalized["evidence_refs"]),
    }
    ensure_json_safe(packet, "reviewer rework packet")
    ensure_secret_free(packet, "reviewer rework packet")
    return packet


def propose_critic(
    runner: ReviewPacketSource,
    provider: Any,
    task_id: str,
    *,
    failure_class: FailureClass | str,
    failure_summary: str,
    failure_spec: ConcreteFailureSpec | Mapping[str, Any] | None = None,
    repair_directive: RepairDirective | Mapping[str, Any] | None = None,
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
        failure_spec=failure_spec,
        repair_directive=repair_directive,
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
        "\n".join(
            (
                f"{finding.location}: {finding.required_correction}",
                f"must preserve: {', '.join(finding.must_preserve) or 'none'}",
                f"forbidden: {', '.join(finding.forbidden_changes) or 'none'}",
                f"acceptance: {', '.join(finding.acceptance_checks) or 'host validation'}",
            )
        )
        for finding in proposal.findings
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
    failure_spec: ConcreteFailureSpec | Mapping[str, Any] | None = None,
    repair_directive: RepairDirective | Mapping[str, Any] | None = None,
    source_attempt_id: str | None = None,
    unresolved_constraints: Sequence[str] = (),
    resolved_constraints: Sequence[str] = (),
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
            failure_spec=failure_spec,
            repair_directive=repair_directive,
            repair_context=(
                _repair_context_for_handoff(
                    plan,
                    failure_evidence_reference=failure_evidence_reference,
                    failure_spec=failure_spec,
                    repair_directive=repair_directive,
                    source_attempt_id=source_attempt_id,
                    unresolved_constraints=unresolved_constraints,
                    resolved_constraints=resolved_constraints,
                )[2]
                if failure_spec is not None or repair_directive is not None
                else None
            ),
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
            failure_spec=failure_spec,
            repair_directive=repair_directive,
            repair_context=(
                _repair_context_for_handoff(
                    plan,
                    failure_evidence_reference=failure_evidence_reference,
                    failure_spec=failure_spec,
                    repair_directive=repair_directive,
                    source_attempt_id=source_attempt_id,
                    unresolved_constraints=unresolved_constraints,
                    resolved_constraints=resolved_constraints,
                )[2]
                if failure_spec is not None or repair_directive is not None
                else None
            ),
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
        repair_context = _repair_context_for_handoff(
            plan,
            failure_evidence_reference=failure_evidence_reference,
            failure_spec=failure_spec,
            repair_directive=repair_directive,
            source_attempt_id=source_attempt_id,
            unresolved_constraints=unresolved_constraints,
            resolved_constraints=resolved_constraints,
        )
        if repair_context is not None:
            latest_spec, latest_directive, context = repair_context
            if not callable(getattr(runner, "rework_handoff", None)):
                raise RefinementCompositionError("runner must expose rework_handoff()")
            handoff = runner.rework_handoff(
                plan.task_id,
                failure_evidence_reference=failure_evidence_reference,
                review_findings_reference=review_findings_reference,
                required_correction=latest_directive.required_action,
                failure_spec=latest_spec,
                repair_directive=latest_directive,
                repair_context=context,
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
                status="REASSIGNED_WITH_REPAIR_HANDOFF",
                executed=True,
                handoff_created=True,
                reason=plan.reasons[0] if plan.reasons else None,
            )
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
    "build_concrete_failure_spec",
    "build_concrete_failure_spec_from_error",
    "build_reviewer_rework_packet",
    "classify_worker_failure",
    "plan_refinement",
    "propose_critic",
]
