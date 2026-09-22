from __future__ import annotations

from typing import Any

import pytest

from scripts.devfarm_refinement import (
    RefinementCompositionError,
    apply_refinement_action,
    build_refinement_packet,
    build_concrete_failure_spec,
    build_concrete_failure_spec_from_error,
    build_reviewer_rework_packet,
    classify_worker_failure,
    plan_refinement,
    propose_critic,
)
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.domain.protocol import IntelligenceTier
from src.dev_agent.intelligence.refinement import (
    CriticFinding,
    FailureClass,
    RefinementAction,
    RefinementContext,
    RefinementPlan,
    RefinementProposal,
)
from src.dev_agent.intelligence.convergence import ConvergenceState
from src.dev_agent.intelligence.convergence import ConcreteFailureSpec, RepairDirective


class _Runner:
    def __init__(self, packet: dict[str, Any]) -> None:
        self.packet = packet
        self.calls: list[str] = []

    def review_packet(self, task_id: str) -> dict[str, Any]:
        self.calls.append(task_id)
        return dict(self.packet)


class _Provider:
    provider_id = "l1-critic-provider"

    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.requests: list[ModelRequest] = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.response


class _ActionRunner:
    def __init__(self) -> None:
        self.handoff_calls: list[dict[str, Any]] = []
        self.reassign_calls: list[dict[str, Any]] = []

    def rework_handoff(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        self.handoff_calls.append({"task_id": task_id, **kwargs})
        return {"kind": "repair_request", "task_id": task_id}

    def reassign(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        self.reassign_calls.append({"task_id": task_id, **kwargs})
        return {"status": "ACTIVE", "task_id": task_id}


def _review_packet() -> dict[str, Any]:
    return {
        "task_id": "production-task-1",
        "attempt_id": "attempt-1",
        "status": "HOST_VERIFIED",
        "changed_files": ["src/dev_agent/coordination/work.py"],
        "patch_sha256": "a" * 64,
        "verification_summary": {
            "host_verified": True,
            "host_tests_passed": False,
        },
        "known_issues": ["the focused verification failed"],
        "acceptance": ["the work item is reconciled"],
        "artifact_refs": [
            {"kind": "verification", "path": ".devfarm/verification/attempt-1.json"},
            {"kind": "result", "path": ".devfarm/results/attempt-1.json"},
        ],
        # These fields must never be copied into the Critic packet.
        "patch": "diff --git a/secret.py b/secret.py",
        "stdout": "credential-like output",
    }


def _response() -> ModelResponse:
    return ModelResponse(
        provider="l1-critic-provider",
        model="l1-critic-model",
        structured_output={
            "task_id": "production-task-1",
            "attempt_id": "attempt-1",
            "findings": [
                {
                    "location": "src/dev_agent/coordination/work.py:10",
                    "problem": "The failure path does not reconcile the work state.",
                    "required_correction": "Persist the reconciliation marker before returning.",
                }
            ],
            "evidence_refs": [".devfarm/verification/attempt-1.json"],
        },
    )


def _refinement_context(**overrides: Any) -> RefinementContext:
    values: dict[str, Any] = {
        "task_id": "production-task-1",
        "failure_class": None,
        "current_tier": IntelligenceTier.L1,
        "allowed_tiers": (IntelligenceTier.L1, IntelligenceTier.L2),
        "attempt": 1,
        "max_attempts": 5,
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
        "alternate_binding_id": "gemini:worker:free-2",
        "current_reasoning_effort": "minimal",
        "allowed_reasoning_efforts": ("minimal", "low", "medium"),
        "model_change_used": False,
        "reasoning_escalated": False,
    }
    values.update(overrides)
    return RefinementContext(**values)


def test_build_refinement_packet_uses_public_review_packet_and_allowlists_evidence():
    runner = _Runner(_review_packet())

    packet = build_refinement_packet(
        runner,
        "production-task-1",
        failure_class=FailureClass.SEMANTIC_TEST,
        failure_summary="Host verification rejected the expected behavior.",
    )

    assert runner.calls == ["production-task-1"]
    assert packet == {
        "task_id": "production-task-1",
        "attempt_id": "attempt-1",
        "failure_class": "semantic_test",
        "failure_summary": "Host verification rejected the expected behavior.",
        "changed_files": ["src/dev_agent/coordination/work.py"],
        "patch_sha256": "a" * 64,
        "evidence_refs": [
            {"kind": "verification", "path": ".devfarm/verification/attempt-1.json"},
            {"kind": "result", "path": ".devfarm/results/attempt-1.json"},
        ],
        "acceptance": ["the work item is reconciled"],
    }
    assert "patch" not in packet
    assert "stdout" not in packet
    assert "verification_summary" not in packet


def test_build_concrete_failure_spec_turns_worker_contract_facts_into_directive():
    spec = build_concrete_failure_spec(
        "file replacement lines must not contain newlines: src/example.py",
        manifest={"allowed_files": ["src/example.py"]},
    )

    assert isinstance(spec, ConcreteFailureSpec)
    assert spec.location == "file_replacements"
    assert "one source line" in spec.required_correction
    assert spec.failure_signature
    assert RepairDirective.from_failure_spec(spec).completion_condition


def test_build_concrete_failure_spec_describes_assumptions_array_mismatch():
    spec = build_concrete_failure_spec("assumptions must be a list")

    assert spec.location == "assumptions"
    assert spec.expected == "array<string>"
    assert "use []" in spec.required_correction
    assert "assumptions is an array" in spec.acceptance_checks


def test_build_concrete_failure_spec_describes_non_string_replacement_line():
    spec = build_concrete_failure_spec("file replacement lines must be strings: src/example.py")

    assert spec.location == "file_replacements"
    assert spec.observed == "replacement line is not a string"
    assert "every replacement line is a string" in spec.acceptance_checks


def test_build_concrete_failure_spec_describes_invalid_json_contract():
    spec = build_concrete_failure_spec("worker response JSON is invalid: malformed")

    assert spec.location == "worker_output"
    assert spec.observed == "invalid JSON object"
    assert "exactly one valid JSON object" in spec.required_correction
    assert "raw newlines inside JSON strings" in spec.forbidden_changes


def test_build_concrete_failure_spec_describes_missing_json_object():
    spec = build_concrete_failure_spec("worker response did not contain a JSON object")

    assert spec.location == "worker_output"
    assert spec.observed == "invalid JSON object"
    assert "response parses as one JSON object" in spec.acceptance_checks


def test_structured_validator_error_builds_exact_bounded_failure_spec():
    spec = build_concrete_failure_spec_from_error(
        {
            "error_code": "WORKER_FILE_REPLACEMENT_EMBEDDED_NEWLINE",
            "location": "file_replacements.src/example.py[18]",
            "observed": "string containing LF",
            "expected": "one line without CR/LF",
            "problem": "a replacement line contains an embedded newline",
            "required_correction": "Split the content into one source line per array element.",
            "acceptance_checks": ("each replacement line contains no LF or CR",),
        }
    )

    assert spec.location == "file_replacements.src/example.py[18]"
    assert spec.validator_refs == ("worker:output_contract", "worker_file_replacement_embedded_newline")
    assert spec.to_dict()["observed"] == "string containing LF"
    assert build_concrete_failure_spec({
        "error_code": "WORKER_FILE_REPLACEMENT_EMBEDDED_NEWLINE",
        "location": "file_replacements.src/example.py[18]",
        "observed": "string containing LF",
        "expected": "one line without CR/LF",
        "problem": "a replacement line contains an embedded newline",
        "required_correction": "Split the content into one source line per array element.",
        "acceptance_checks": ("each replacement line contains no LF or CR",),
    }).failure_signature == spec.failure_signature
def test_refinement_packet_carries_concrete_failure_spec_without_raw_output():
    runner = _Runner(_review_packet())
    spec = build_concrete_failure_spec(
        "known_issues must be a list",
        manifest={"allowed_files": ["src/dev_agent/coordination/work.py"]},
    )

    packet = build_refinement_packet(
        runner,
        "production-task-1",
        failure_class=FailureClass.FORMAT_PATCH,
        failure_summary=spec.problem,
        failure_spec=spec,
        repair_directive=RepairDirective.from_failure_spec(spec),
    )

    assert packet["failure_spec"]["location"] == "known_issues"
    assert packet["repair_directive"]["repair_target"] == "known_issues"
    assert "raw_output" not in packet
    assert "stdout" not in packet


def test_propose_critic_composes_public_packet_with_host_selected_l1_provider():
    runner = _Runner(_review_packet())
    provider = _Provider(_response())

    proposal = propose_critic(
        runner,
        provider,
        "production-task-1",
        failure_class="semantic_test",
        failure_summary="Host verification rejected the expected behavior.",
    )

    assert isinstance(proposal, RefinementProposal)
    assert proposal.task_id == "production-task-1"
    assert proposal.attempt_id == "attempt-1"
    assert len(provider.requests) == 1
    assert provider.requests[0].metadata["model_selection"] == "host_selected"
    assert provider.requests[0].metadata["allowed_intelligence_tiers"] == ["L1"]


def test_propose_critic_can_move_concrete_critic_call_to_host_executor():
    runner = _Runner(_review_packet())
    provider = _Provider(_response())
    provider.provider_id = "fake"
    calls: list[tuple[object, str]] = []

    def host_execute(selected: object, request: ModelRequest) -> ModelResponse:
        calls.append((selected, request.request_id))
        return _response()

    proposal = propose_critic(
        runner,
        provider,
        "production-task-1",
        failure_class="semantic_test",
        failure_summary="Host verification rejected the expected behavior.",
        execution_boundary="host_process",
        host_executor=host_execute,
    )

    assert isinstance(proposal, RefinementProposal)
    assert len(calls) == 1
    assert calls[0][0] is provider
    assert provider.requests == []


def test_build_refinement_packet_rejects_runner_identity_mismatch_before_provider_use():
    runner = _Runner({**_review_packet(), "task_id": "another-task"})

    with pytest.raises(RefinementCompositionError, match="task_id"):
        build_refinement_packet(
            runner,
            "production-task-1",
            failure_class="format_patch",
            failure_summary="The patch could not be applied.",
        )


def test_classify_worker_failure_keeps_format_semantic_provider_and_security_distinct():
    assert classify_worker_failure("patch_format_failure") is FailureClass.FORMAT_PATCH
    assert classify_worker_failure("model_output_invalid") is FailureClass.FORMAT_PATCH
    assert classify_worker_failure("host_verification_failure") is FailureClass.SEMANTIC_TEST
    assert classify_worker_failure("contract_shape_regression") is FailureClass.SEMANTIC_TEST
    assert classify_worker_failure("provider_unavailable") is FailureClass.PROVIDER_TRANSPORT
    assert classify_worker_failure("scope_violation") is FailureClass.SECURITY_EGRESS_AUTHORITY


def test_classify_worker_failure_closes_ambiguous_external_effect_to_reconciliation():
    assert classify_worker_failure("timeout", external_outcome_known=False) is FailureClass.UNKNOWN_EXTERNAL_EFFECT
    assert classify_worker_failure("unknown_external_effect") is FailureClass.UNKNOWN_EXTERNAL_EFFECT
    assert classify_worker_failure("timeout", external_outcome_known=True) is FailureClass.PROVIDER_TRANSPORT


def test_classify_worker_failure_rejects_unknown_categories_instead_of_guessing():
    with pytest.raises(RefinementCompositionError, match="unsupported failure category"):
        classify_worker_failure("some_future_failure")


def test_plan_refinement_connects_host_category_to_existing_bounded_policy():
    plan = plan_refinement(
        _refinement_context(),
        "patch_format_failure",
    )

    assert plan.action is RefinementAction.CORRECT
    assert plan.failure_class is FailureClass.FORMAT_PATCH
    assert plan.refinement_round == 1


def test_plan_refinement_records_format_failure_convergence_without_raw_evidence():
    plan = plan_refinement(
        _refinement_context(),
        "patch_format_failure",
        source_attempt_id="attempt-source",
        current_model_identity="l1:worker-a",
        validator_refs=("patch:parse",),
        patch_category="unified_diff",
        error_code="HUNK_APPLY_FAILED",
    )

    assert plan.convergence is not None
    assert plan.convergence.convergence_state is ConvergenceState.REFINEMENT
    assert plan.convergence.source_attempt_id == "attempt-source"
    assert plan.convergence.failure_class == "format_patch"
    assert plan.convergence.validator_refs == ("patch:parse",)
    assert len(plan.convergence.failure_signature) == 64
    assert "HUNK" not in plan.to_dict()["convergence"]["failure_signature"]


def test_plan_refinement_records_semantic_failure_test_ids_and_round_trips():
    plan = plan_refinement(
        _refinement_context(),
        "contract_shape_regression",
        source_attempt_id="attempt-semantic",
        current_model_identity="l1:worker-a",
        test_ids=("tests/v2/test_contract.py::test_shape",),
    )

    restored = RefinementPlan.from_dict(plan.to_dict())
    assert restored.convergence == plan.convergence
    assert restored.convergence.failure_class == "semantic_test"
    assert restored.convergence.validator_refs == ("worker:host_verification",)
    assert len(restored.convergence.failure_signature) == 64


def test_plan_refinement_keeps_external_failures_out_of_convergence_lane():
    plan = plan_refinement(
        _refinement_context(external_outcome_known=False),
        "provider_unavailable",
    )

    assert plan.action is RefinementAction.RECONCILE
    assert plan.convergence is None


def test_reviewer_approve_is_a_fast_path_without_rework_packet():
    runner = _Runner(_review_packet())

    packet = build_reviewer_rework_packet(
        runner,
        "production-task-1",
        {
            "decision_id": "decision-approve",
            "task_id": "production-task-1",
            "attempt_id": "attempt-1",
            "decision": "APPROVE_INTEGRATION",
            "findings": [],
            "evidence_refs": [],
        },
    )

    assert packet is None
    assert runner.calls == []


def test_reviewer_rework_packet_is_reference_first_and_matches_current_attempt():
    runner = _Runner(_review_packet())

    packet = build_reviewer_rework_packet(
        runner,
        "production-task-1",
        {
            "decision_id": "decision-rework",
            "task_id": "production-task-1",
            "attempt_id": "attempt-1",
            "decision": "REWORK",
            "findings": ["the proposal does not satisfy the contract"],
            "required_correction": "Align the implementation with the stated contract.",
            "evidence_refs": [{"kind": "review", "path": ".devfarm/reviews/attempt-1.json"}],
        },
    )

    assert packet is not None
    assert packet["required_correction"].startswith("Align")
    assert packet["review_findings_reference"] == {
        "kind": "review_findings",
        "decision_id": "decision-rework",
        "task_id": "production-task-1",
        "attempt_id": "attempt-1",
        "finding_count": 1,
        "evidence_refs": [{"kind": "review", "path": ".devfarm/reviews/attempt-1.json"}],
    }
    assert "findings" not in packet["review_findings_reference"]


def test_plan_refinement_preserves_unknown_external_effect_boundary():
    plan = plan_refinement(
        _refinement_context(external_outcome_known=False),
        "provider_unavailable",
    )

    assert plan.action is RefinementAction.RECONCILE
    assert plan.requires_reconciliation is True
    assert plan.next_binding_id is None


def test_plan_refinement_rejects_unknown_category_before_policy_execution():
    with pytest.raises(RefinementCompositionError, match="unsupported failure category"):
        plan_refinement(_refinement_context(), "future_failure")


def test_apply_refinement_action_formats_one_correction_without_thinking_escalation():
    runner = _ActionRunner()
    plan = plan_refinement(_refinement_context(), "patch_format_failure")

    result = apply_refinement_action(
        runner,
        plan,
        failure_evidence_reference={"kind": "verification", "path": ".devfarm/failure.json"},
        required_correction="Regenerate the bounded unified diff with the exact file context.",
        assignment={
            "provider_id": "cloudflare",
            "model_id": "@cf/meta/llama-3.1-8b-instruct",
            "provider_binding_id": "cloudflare",
        },
    )

    assert result.action is RefinementAction.CORRECT
    assert result.executed is True
    assert result.thinking_escalated is False
    assert len(runner.handoff_calls) == 1
    assert len(runner.reassign_calls) == 1
    assert runner.reassign_calls[0]["rework_handoff"] == {"kind": "repair_request", "task_id": "production-task-1"}


def test_apply_refinement_action_uses_one_l1_critic_proposal_for_rework_only():
    runner = _ActionRunner()
    plan = plan_refinement(_refinement_context(), "host_verification_failure")
    proposal = RefinementProposal(
        task_id="production-task-1",
        attempt_id="attempt-1",
        findings=(
            CriticFinding(
                location="src/dev_agent/coordination/work.py:10",
                problem="The reconciliation marker is not persisted.",
                required_correction="Persist the marker before returning.",
            ),
        ),
        evidence_refs=(".devfarm/verification/attempt-1.json",),
    )

    result = apply_refinement_action(
        runner,
        plan,
        failure_evidence_reference={"kind": "verification", "path": ".devfarm/failure.json"},
        critic_proposal=proposal,
        assignment={
            "provider_id": "cloudflare",
            "model_id": "@cf/meta/llama-3.1-8b-instruct",
            "provider_binding_id": "cloudflare",
        },
    )

    assert result.action is RefinementAction.CRITIQUE
    assert result.executed is True
    assert len(runner.handoff_calls) == 1
    assert len(runner.reassign_calls) == 1
    assert runner.handoff_calls[0]["review_findings_reference"]["kind"] == "refinement_proposal"
    assert "Persist the marker" in runner.handoff_calls[0]["required_correction"]


def test_reassign_same_tier_rebinds_latest_failure_and_directive_for_fallback():
    runner = _ActionRunner()
    plan = RefinementPlan(
        plan_id="00000000-0000-4000-8000-000000000001",
        task_id="production-task-1",
        action=RefinementAction.REASSIGN_SAME_TIER,
        failure_class=FailureClass.FORMAT_PATCH,
        attempt=4,
        refinement_round=3,
        reasons=("recurrent_failure_cycle",),
        next_binding_id="gemma-local",
    )
    spec = build_concrete_failure_spec("worker response JSON is invalid: extra data")
    directive = RepairDirective.from_failure_spec(spec)

    result = apply_refinement_action(
        runner,
        plan,
        failure_evidence_reference={"kind": "verification", "path": ".devfarm/failure-3.json", "attempt_id": "attempt-3"},
        failure_spec=spec,
        repair_directive=directive,
        unresolved_constraints=("response must be one JSON object",),
        resolved_constraints=("embedded newline",),
        assignment={
            "provider_id": "ollama",
            "model_id": "gemma4:12b",
            "provider_binding_id": plan.next_binding_id,
        },
    )

    assert result.status == "REASSIGNED_WITH_REPAIR_HANDOFF"
    assert result.handoff_created is True
    assert len(runner.handoff_calls) == 1
    assert len(runner.reassign_calls) == 1
    handoff = runner.handoff_calls[0]
    context = handoff["repair_context"]
    assert context["source_attempt_id"] == "attempt-3"
    assert context["source_failure_signature"] == spec.failure_signature
    assert context["repair_directive_hash"] == directive.directive_hash
    assert context["directive_rebound"] is True
    assert context["current_repair"]["repair_target"] == "worker_output"
    assert context["historical_constraints"]["resolved_must_not_regress"] == ["embedded newline"]
    assert runner.reassign_calls[0]["rework_handoff"] == {"kind": "repair_request", "task_id": "production-task-1"}


def test_reassign_same_tier_does_not_create_repair_handoff_for_provider_failure():
    runner = _ActionRunner()
    plan = plan_refinement(
        _refinement_context(external_outcome_known=True),
        "provider_unavailable",
    )

    result = apply_refinement_action(
        runner,
        plan,
        assignment={
            "provider_id": "ollama",
            "model_id": "gemma4:12b",
            "provider_binding_id": plan.next_binding_id,
        },
    )

    assert result.action is RefinementAction.REASSIGN_SAME_TIER
    assert result.handoff_created is False
    assert runner.handoff_calls == []
