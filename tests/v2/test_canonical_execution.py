from __future__ import annotations

import pytest
from uuid import uuid4

from src.dev_agent.domain.execution import (
    CanonicalExecutionRequest,
    CanonicalExecutionResult,
    ExecutionRequirement,
    FieldPresence,
    PresenceState,
    UnsupportedCapabilityError,
)
from src.dev_agent.domain.protocol import IntelligenceTier, ModelRequest, ModelResponse
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import (
    EffectiveRouteDecision,
    ResourceRouter,
    RouteDecisionCode,
)


def _local_ledger(tmp_path):
    ledger = ResourceLedger(tmp_path / "canonical-execution.sqlite3")
    ledger.register_resource(
        "ollama-worker",
        provider_id="ollama",
        provider_binding_id="ollama:local:qwen3.5-9b",
        native_unit="request",
        capacity=1,
        capabilities=["text", "structured_output"],
        sensitivity="normal",
        cost_minor=0,
        metadata={
            "provider_binding_id": "ollama:local:qwen3.5-9b",
            "model_id": "qwen3.5:9b",
            "intelligence_tier": "L1",
        },
    )
    ledger.observe("ollama-worker", available=1, health="healthy", concurrency_limit=1)
    return ledger


def test_field_presence_preserves_unspecified_value_and_explicit_none():
    unspecified = FieldPresence.unspecified()
    value = FieldPresence.value(42)
    explicit_none = FieldPresence.explicit_none()

    assert unspecified.state is PresenceState.UNSPECIFIED
    assert value.state is PresenceState.VALUE
    assert value.payload == 42
    assert explicit_none.state is PresenceState.EXPLICIT_NONE
    assert explicit_none.payload is None
    assert FieldPresence.from_dict(value.to_dict()) == value
    assert FieldPresence.from_dict(explicit_none.to_dict()) == explicit_none
    with pytest.raises(ValueError, match="VALUE cannot contain None"):
        FieldPresence.value(None)


def test_execution_requirement_normalizes_semantically_equivalent_capabilities():
    first = ExecutionRequirement(
        role="worker",
        required_capabilities=("tool_call", "text", "tool_call"),
        feature_requirements={"seed": FieldPresence.value(42)},
    )
    second = ExecutionRequirement(
        role="worker",
        required_capabilities=("text", "tool_call"),
        feature_requirements={"seed": FieldPresence.value(42)},
    )

    assert first.required_capabilities == ("text", "tool_call")
    assert first.digest() == second.digest()
    assert first.to_dict()["feature_requirements"]["seed"]["state"] == "VALUE"


def test_unsupported_capability_is_typed_and_explicit_none_is_not_a_requirement():
    requirement = ExecutionRequirement(
        required_capabilities=("text",),
        feature_requirements={
            "seed": FieldPresence.value(42),
            "thinking": FieldPresence.explicit_none(),
        },
    )

    with pytest.raises(UnsupportedCapabilityError) as raised:
        requirement.require_supported_capabilities({"text"})

    assert raised.value.code == "UNSUPPORTED_CAPABILITY"
    assert raised.value.capabilities == ("seed",)


def test_canonical_request_and_result_reuse_existing_provider_neutral_protocol():
    assert CanonicalExecutionRequest is ModelRequest
    assert CanonicalExecutionResult is ModelResponse


def test_effective_route_rejects_l1_resource_for_l2_requirement_without_dispatch(tmp_path):
    ledger = _local_ledger(tmp_path)
    requirement = ExecutionRequirement(
        role="planner",
        minimum_intelligence_tier=IntelligenceTier.L2,
        required_capabilities=("text",),
    )

    decision = ResourceRouter(ledger).decide(requirement)

    assert isinstance(decision, EffectiveRouteDecision)
    assert decision.eligible is False
    assert decision.selection is None
    assert RouteDecisionCode.INTELLIGENCE_TIER_UNSATISFIED in decision.reasons
    assert RouteDecisionCode.ELIGIBLE not in decision.reasons


def test_effective_route_accepts_same_l1_resource_for_l1_worker(tmp_path):
    ledger = _local_ledger(tmp_path)
    requirement = ExecutionRequirement(
        role="implementer",
        minimum_intelligence_tier=IntelligenceTier.L1,
        required_capabilities=("text",),
    )

    decision = ResourceRouter(ledger).decide(requirement)

    assert decision.eligible is True
    assert decision.selection is not None
    assert decision.selection.model_id == "qwen3.5:9b"
    assert decision.reasons == (RouteDecisionCode.ELIGIBLE,)
    assert decision.evidence["health"] == "healthy"


def test_control_plane_exposes_the_same_effective_route_as_router(tmp_path):
    ledger = _local_ledger(tmp_path)
    request = ModelRequest(
        task_id=str(uuid4()),
        requested_capabilities=["text"],
        metadata={
            "task_type": "worker",
            "execution_role": "implementer",
            "minimum_intelligence_tier": "L1",
            "intelligence_routing": "bounded",
            "allowed_intelligence_tiers": ["L1"],
        },
    )
    requirement = ExecutionRequirement.from_model_request(request)
    router_decision = ResourceRouter(ledger).decide(requirement)
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0))
    control_decision = ResourceControlPlane(
        ResourceRouter(ledger),
        BudgetGovernor(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0)),
    ).effective_route(request)

    assert control_decision.to_dict()["eligible"] is True
    assert control_decision.selection == router_decision.selection
    assert control_decision.requirement_digest == router_decision.requirement_digest


def test_model_request_defaults_optional_requirement_fields_to_unspecified():
    request = ModelRequest(task_id=str(uuid4()), requested_capabilities=["text"])
    requirement = ExecutionRequirement.from_model_request(request)

    assert requirement.billing_policy.state is PresenceState.UNSPECIFIED
    assert requirement.quota_policy.state is PresenceState.UNSPECIFIED
    assert requirement.freshness_policy.state is PresenceState.UNSPECIFIED
    assert requirement.feature_requirements == {}
