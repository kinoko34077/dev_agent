import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ModelResponse, TaskType
from src.dev_agent.intelligence.planner import PlanningValidationError, RootPlanningProposal
from src.dev_agent.intelligence.planner_adapter import (
    ModelPlanningAdapter,
    PlanningAdapterError,
    PlanningResponseError,
)
from src.dev_agent.operation import OperationConfig, OperationService
from src.dev_agent.providers.base import ModelProvider, ProviderError
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.qualification import QualificationResolver
from src.dev_agent.resources.router import ResourceRouter
import scripts.devfarm_planner_shadow as planner_shadow
from scripts.devfarm_planner_shadow import (
    PlannerShadowBlocked,
    PlannerShadowInputError,
    resolve_provider_pool,
    validate_parent_task_id,
)
from scripts.devfarm_resource_pool import ResourcePoolError


class _Provider:
    provider_id = "planner-test"

    def __init__(self, response: ModelResponse):
        self.response = response
        self.requests: list[ModelRequest] = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.response


class _RoutedPlannerProvider(ModelProvider):
    provider_id = "gemini"

    def __init__(self, parent_task_id: str):
        self.model = "gemini-3.8-flash"
        self.provider_binding_id = "gemini:core"
        self.parent_task_id = parent_task_id
        self.requests: list[ModelRequest] = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            provider=self.provider_id,
            model=self.model,
            structured_output=_payload(self.parent_task_id),
            usage={"cost_minor": 0},
        )


def _payload(parent_task_id: str) -> dict:
    return {
        "parent_task_id": parent_task_id,
        "rationale": "split the bounded work into one implementation child",
        "planning_cycle": 1,
        "proposal_id": "planner-proposal-1",
        "children": [
            {
                "child_key": "implementation",
                "objective": "implement the narrow change",
                "task_type": "worker",
                "risk": "low",
                "sensitivity": "normal",
                "required_capabilities": ["coding"],
                "dependencies": [],
                "dependency_types": {},
                "suggested_owner": "worker",
            }
        ],
    }


def test_model_planner_returns_typed_proposal_and_keeps_host_authority(tmp_path):
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            structured_output=_payload(parent_task_id),
        )
    )
    adapter = ModelPlanningAdapter(provider)

    proposal = adapter.propose(
        parent_task_id=parent_task_id,
        objective="split this narrow development objective",
        sensitivity="normal",
        context_references={"repository": "kinoko34077/dev_agent", "branch": "v2/bootstrap"},
    )

    assert proposal.parent_task_id == parent_task_id
    assert proposal.children[0].child_key == "implementation"
    assert proposal.children[0].task_type.value == "worker"
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.requested_capabilities == ["text"]
    assert request.response_schema is not None
    assert request.metadata["planning_mode"] == "proposal_only"
    assert request.metadata["authority"] == "host_validation_required"
    assert "split this narrow development objective" in request.messages[0]["content"]
    assert "v2/bootstrap" in request.messages[0]["content"]
    assert "exactly one raw JSON object" in request.messages[0]["content"]
    assert "Markdown fences" in request.messages[0]["content"]
    assert "trailing text" in request.messages[0]["content"]
    assert "Every child field" in request.messages[0]["content"]
    assert "do not place child fields at the top level" in request.messages[0]["content"]
    assert "suggested_owner` exactly as `worker` or `codex`" in request.messages[0]["content"]
    assert "Use only these exact required_capabilities values" in request.messages[0]["content"]
    assert '"children":[{"child_key"' in request.messages[0]["content"]


def test_model_planner_schema_constrains_host_enums_before_provider_generation():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            structured_output=_payload(parent_task_id),
        )
    )

    ModelPlanningAdapter(provider).propose(
        parent_task_id=parent_task_id,
        objective="split this narrow development objective",
    )

    child_properties = provider.requests[0].response_schema["properties"]["children"]["items"]["properties"]
    assert child_properties["task_type"]["enum"] == [
        "delegated_agent",
        "deterministic",
        "expert",
        "protected",
        "reasoning",
        "recovery",
        "worker",
    ]
    assert child_properties["risk"]["enum"] == ["critical", "high", "low", "normal"]
    assert child_properties["sensitivity"]["enum"] == [None, "internal", "normal", "public", "sensitive"]
    assert child_properties["suggested_owner"]["enum"] == ["codex", "worker"]


def test_phase8_planner_profile_accepts_only_bounded_objectives_and_host_composes_shape():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="qwen3.5:9b",
            structured_output={
                "worker_a_objective": "Modify only src/a.py to add the bounded behavior.",
                "worker_b_objective": "Add the focused regression test in tests/v2/test_a.py.",
                "continuation_objective": "Run the existing integration continuation after both workers pass.",
            },
        )
    )

    proposal = ModelPlanningAdapter(provider, proposal_profile="phase8_production").propose(
        parent_task_id=parent_task_id,
        objective="complete one small Phase 8 production composition",
    )

    request = provider.requests[0]
    assert set(request.response_schema["properties"]) == {
        "worker_a_objective",
        "worker_b_objective",
        "continuation_objective",
    }
    assert request.response_schema["additionalProperties"] is False
    assert request.metadata["planner_contract"] == "phase8_minimal_root_v1"
    assert [child.child_key for child in proposal.children] == ["worker-a", "worker-b", "continuation"]
    assert [child.task_type.value for child in proposal.children] == ["worker", "worker", "deterministic"]
    assert proposal.children[2].suggested_owner == "codex"
    assert proposal.children[2].dependencies == ("worker-a", "worker-b")
    assert set(proposal.children[2].dependency_types) == {"worker-a", "worker-b"}


def test_phase8_planner_profile_rejects_unknown_fields_with_bounded_failure_spec():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="qwen3.5:9b",
            structured_output={
                "worker_a_objective": "Modify only src/a.py.",
                "worker_b_objective": "Add the focused regression test.",
                "continuation_objective": "Continue after integration.",
                "suggested_owner": "codex",
            },
        )
    )

    with pytest.raises(PlanningResponseError) as raised:
        ModelPlanningAdapter(provider, proposal_profile="phase8_production").propose(
            parent_task_id=parent_task_id,
            objective="complete one small Phase 8 production composition",
        )

    spec = raised.value.failure_spec
    assert spec is not None
    assert spec.location == "phase8_payload"
    assert "unsupported" in spec.required_correction
    assert "phase8:error:phase8_invalid_fields" in spec.validator_refs


def test_model_planner_projects_unknown_child_field_to_bounded_failure_location():
    parent_task_id = str(uuid4())
    payload = _payload(parent_task_id)
    payload["children"][0]["notes"] = "unsupported"
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            structured_output=payload,
        )
    )

    with pytest.raises(PlanningResponseError) as raised:
        ModelPlanningAdapter(provider).propose(
            parent_task_id=parent_task_id,
            objective="split this narrow development objective",
        )

    spec = raised.value.failure_spec
    assert spec is not None
    assert spec.location == "children[].notes"
    assert "unsupported" in spec.required_correction
    assert "planner:error:unknown_child_field" in spec.validator_refs


def test_model_planner_can_require_an_exact_l2_route_without_model_name_inference():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="planner-model",
            structured_output=_payload(parent_task_id),
        )
    )

    ModelPlanningAdapter(provider).propose(
        parent_task_id=parent_task_id,
        objective="split this narrow development objective",
        required_intelligence_tier="L2",
    )

    request = provider.requests[0]
    assert request.metadata["intelligence_routing"] == "bounded"
    assert request.metadata["allowed_intelligence_tiers"] == ["L2"]


def test_model_planner_can_use_existing_dispatcher_for_exact_qualified_l2(tmp_path):
    parent_task_id = str(uuid4())
    now = datetime.now(timezone.utc)
    qualification = QualificationResolver(
        entries=[
            {
                "provider": "gemini",
                "provider_binding_id": "gemini:core",
                "model": "gemini-3.8-flash",
                "intelligence_tier": "L2",
                "confidence": "high",
                "tested_at": (now - timedelta(minutes=1)).isoformat(),
                "expires_at": (now + timedelta(hours=1)).isoformat(),
                "capabilities": [
                    "text",
                    "model_generated_tool_call",
                    "tool_result_roundtrip",
                    "final_response",
                ],
            }
        ]
    )
    provider = _RoutedPlannerProvider(parent_task_id)
    ledger = ResourceLedger(tmp_path / "planner-routing.sqlite3")
    try:
        ledger.register_resource(
            "gemini:core",
            provider_id="gemini",
            provider_binding_id="gemini:core",
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            sensitivity="normal",
            cost_minor=0,
            price_currency="JPY",
            quota_domain="gemini-core-account",
            intelligence_tier="L2",
            metadata={
                "provider_binding_id": "gemini:core",
                "model_id": "gemini-3.8-flash",
                "intelligence_tier": "L2",
                "privacy_profile": "remote_cloud",
                "qualification_required": True,
                "billing_authority": "trusted_catalog",
                "billing_mode": "recurring_allowance",
                "overage_policy": "hard_stop",
                "no_charge_guaranteed": True,
                "billing_expires_at": (now + timedelta(hours=1)).isoformat(),
            },
        )
        ledger.observe("gemini:core", available=1, health="healthy", concurrency_limit=1)
        BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0))
        dispatcher = ProviderDispatcher(
            ProviderRegistry([provider]),
            ResourceControlPlane(ResourceRouter(ledger, qualification_resolver=qualification), BudgetGovernor(ledger)),
        )

        proposal = ModelPlanningAdapter(dispatcher, allow_unknown_quota=True).propose(
            parent_task_id=parent_task_id,
            objective="split this narrow development objective",
            required_intelligence_tier="L2",
        )

        assert proposal.parent_task_id == parent_task_id
        assert len(provider.requests) == 1
        assert provider.requests[0].metadata["allowed_intelligence_tiers"] == ["L2"]
        assert len(provider.requests[0].metadata["egress_manifest_sha256"]) == 64
        assert provider.requests[0].metadata["egress_manifest_policy"] == "dev-agent-model-request-egress-v1"
    finally:
        ledger.close()


def test_model_planner_accepts_bounded_json_text_when_provider_has_no_structured_field():
    parent_task_id = str(uuid4())
    payload = _payload(parent_task_id)
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            text_segments=[json.dumps(payload)],
        )
    )

    proposal = ModelPlanningAdapter(provider).propose(parent_task_id=parent_task_id, objective="bounded objective")

    assert proposal.proposal_id == "planner-proposal-1"


def test_model_planner_rejects_narrative_or_wrong_parent_as_non_authoritative_output():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            text_segments=["I recommend three tasks, but here is no JSON."],
        )
    )
    with pytest.raises(PlanningAdapterError, match="JSON"):
        ModelPlanningAdapter(provider).propose(parent_task_id=parent_task_id, objective="bounded objective")

    wrong_parent = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            structured_output=_payload(wrong_parent),
        )
    )
    with pytest.raises(PlanningResponseError, match="parent_task_id") as raised:
        ModelPlanningAdapter(provider).propose(parent_task_id=parent_task_id, objective="bounded objective")
    assert raised.value.request_id == provider.requests[0].request_id
    assert raised.value.provider_response_observed is True


def test_model_planner_binds_response_contract_failure_to_fresh_request_id():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            text_segments=["not-json"],
        )
    )

    with pytest.raises(PlanningResponseError) as raised:
        ModelPlanningAdapter(provider).propose(
            parent_task_id=parent_task_id,
            objective="bounded objective",
        )

    request_id = getattr(raised.value, "request_id", None)
    assert request_id == provider.requests[0].request_id
    assert raised.value.provider_response_observed is True
    assert raised.value.response_contract == "invalid_json"
    assert raised.value.failure_spec is not None
    assert raised.value.failure_spec.location == "response"
    assert "second JSON object" in raised.value.failure_spec.required_correction


def test_model_planner_marks_schema_failures_as_invalid_proposal():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            structured_output=_payload(str(uuid4())),
        )
    )

    with pytest.raises(PlanningResponseError) as raised:
        ModelPlanningAdapter(provider).propose(
            parent_task_id=parent_task_id,
            objective="bounded objective",
        )

    assert raised.value.response_contract == "invalid_proposal"


def test_model_planner_binds_provider_failure_to_fresh_request_id():
    parent_task_id = str(uuid4())

    class _FailingProvider(_Provider):
        def request(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            raise ProviderError(
                "transport detail must remain outside the adapter projection",
                category="transport",
                retryable=True,
            )

    provider = _FailingProvider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            text_segments=["unused"],
        )
    )

    with pytest.raises(ProviderError) as raised:
        ModelPlanningAdapter(provider).propose(
            parent_task_id=parent_task_id,
            objective="bounded objective",
        )

    assert raised.value.request_id == provider.requests[0].request_id


def test_typed_proposal_round_trip_rejects_untracked_fields():
    parent_task_id = str(uuid4())
    payload = _payload(parent_task_id)
    proposal = RootPlanningProposal.from_dict(payload)

    assert RootPlanningProposal.from_dict(proposal.to_dict()).to_dict() == proposal.to_dict()
    payload["untracked_authority"] = "must not be accepted"
    with pytest.raises(PlanningValidationError, match="unknown planning proposal field"):
        RootPlanningProposal.from_dict(payload)


def test_planner_shadow_reports_bounded_host_transport_category(monkeypatch, capsys):
    request_id = str(uuid4())
    failure = ProviderError(
        "secret-shaped transport detail must not cross the projection",
        category="reconciliation_required",
        retryable=False,
    )
    failure.transport_failure_category = "sandbox_network_denied"
    failure.request_id = request_id
    monkeypatch.setattr(
        planner_shadow.ModelEvidenceCatalog,
        "load_default",
        lambda: type("Evidence", (), {"resolver": object(), "catalog": object()})(),
    )
    monkeypatch.setattr(planner_shadow, "resolve_provider_pool", lambda **_kwargs: None)

    def fail_shadow(**_kwargs):
        raise failure

    monkeypatch.setattr(planner_shadow, "run_shadow", fail_shadow)

    assert planner_shadow.main(["--objective", "diagnostic"]) == 2

    output = json.loads(capsys.readouterr().out)
    assert output["category"] == "reconciliation_required"
    assert output["reconciliation_required"] is True
    assert output["transport_failure_category"] == "sandbox_network_denied"
    assert output["error_type"] == "ProviderError"
    assert "message" not in output
    assert "secret-shaped transport detail" not in json.dumps(output)
    assert output["request_id"] == request_id


def test_planner_shadow_projects_admission_no_route_as_local_block(monkeypatch, capsys):
    monkeypatch.setattr(
        planner_shadow.ModelEvidenceCatalog,
        "load_default",
        lambda: type("Evidence", (), {"resolver": object(), "catalog": object()})(),
    )
    monkeypatch.setattr(planner_shadow, "resolve_provider_pool", lambda **_kwargs: None)
    monkeypatch.setattr(
        planner_shadow,
        "run_shadow",
        lambda **_kwargs: (_ for _ in ()).throw(
            PlannerShadowBlocked("private admission detail", category="no_route")
        ),
    )

    assert planner_shadow.main(["--objective", "diagnostic"]) == 2

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "category": "no_route",
        "error_type": "PlannerShadowBlocked",
        "parent_task_id": output["parent_task_id"],
        "reconciliation_required": False,
        "status": "blocked_local",
    }
    assert "private admission detail" not in json.dumps(output)


def test_planner_shadow_projects_pool_composition_without_external_reconciliation(monkeypatch, capsys):
    secret_detail = "resource composition detail with credential-shaped content"
    monkeypatch.setattr(
        planner_shadow.ModelEvidenceCatalog,
        "load_default",
        lambda: type("Evidence", (), {"resolver": object(), "catalog": object()})(),
    )
    monkeypatch.setattr(planner_shadow, "resolve_provider_pool", lambda **_kwargs: None)
    monkeypatch.setattr(
        planner_shadow,
        "run_shadow",
        lambda **_kwargs: (_ for _ in ()).throw(ResourcePoolError(secret_detail)),
    )

    assert planner_shadow.main(["--objective", "diagnostic"]) == 2

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "category": "resource_pool_composition",
        "error_type": "ResourcePoolError",
        "parent_task_id": output["parent_task_id"],
        "reconciliation_required": False,
        "status": "blocked_local",
    }
    assert secret_detail not in json.dumps(output)


def test_planner_shadow_classifies_invalid_model_output(monkeypatch, capsys):
    parent_task_id = str(uuid4())
    monkeypatch.setattr(
        planner_shadow.ModelEvidenceCatalog,
        "load_default",
        lambda: type("Evidence", (), {"resolver": object(), "catalog": object()})(),
    )
    monkeypatch.setattr(planner_shadow, "resolve_provider_pool", lambda **_kwargs: None)
    monkeypatch.setattr(
        planner_shadow,
        "run_shadow",
        lambda **_kwargs: (_ for _ in ()).throw(
            PlanningResponseError(
                "planner response is not valid JSON",
                request_id="2e6f2d5c-8cf5-4b35-bc47-7c8f20ed6f04",
                provider_response_observed=True,
                response_contract="invalid_json",
            )
        ),
    )

    assert planner_shadow.main(["--objective", "diagnostic", "--parent-task-id", parent_task_id]) == 2

    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "failed"
    assert output["category"] == "model_output_invalid"
    assert output["adapter_error"] == "PlanningResponseError"
    assert output["provider_response_observed"] is True
    assert output["reconciliation_required"] is False
    assert output["response_contract"] == "invalid_json"
    assert output["request_id"] == "2e6f2d5c-8cf5-4b35-bc47-7c8f20ed6f04"
    assert output["parent_task_id"] == parent_task_id


def test_planner_shadow_cli_passes_bounded_output_ceiling(monkeypatch, capsys):
    captured = {}
    monkeypatch.setattr(
        planner_shadow.ModelEvidenceCatalog,
        "load_default",
        lambda: type("Evidence", (), {"resolver": object(), "catalog": object()})(),
    )
    monkeypatch.setattr(planner_shadow, "resolve_provider_pool", lambda **_kwargs: None)

    def capture_shadow(**kwargs):
        captured.update(kwargs)
        return {"status": "pool_exhausted"}

    monkeypatch.setattr(planner_shadow, "run_shadow", capture_shadow)

    assert planner_shadow.main(["--objective", "bounded", "--max-output-tokens", "4096"]) == 2
    json.loads(capsys.readouterr().out)
    assert captured["max_output_tokens"] == 4096


def test_planner_shadow_redacts_unexpected_failure_details(monkeypatch, capsys):
    secret_detail = "unexpected secret-shaped planner failure"
    monkeypatch.setattr(
        planner_shadow.ModelEvidenceCatalog,
        "load_default",
        lambda: (_ for _ in ()).throw(RuntimeError(secret_detail)),
    )

    assert planner_shadow.main(["--objective", "diagnostic"]) == 2

    output = json.loads(capsys.readouterr().out)
    assert output == {
        "category": "planner_shadow_failure",
        "error_type": "RuntimeError",
        "parent_task_id": output["parent_task_id"],
        "reconciliation_required": True,
        "status": "failed",
    }
    assert secret_detail not in json.dumps(output)


def test_planner_output_is_only_a_proposal_until_operation_host_validation(tmp_path):
    config = OperationConfig(
        data_dir=tmp_path,
        provider_id="fake",
        model="deterministic",
        worker_id="planner-shadow-test",
        idle_sleep_seconds=0.01,
    )
    parent = OperationService.submit(config, "coordinate a bounded planner shadow", task_type=TaskType.REASONING)
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            structured_output=_payload(parent.task_id),
        )
    )
    proposal = ModelPlanningAdapter(provider).propose(
        parent_task_id=parent.task_id,
        objective=parent.objective,
    )

    with OperationService.open(config) as service:
        accepted = service.validate_planning_proposal(proposal)

    assert len(accepted) == 1
    assert accepted[0].child_key == "implementation"
    assert OperationService.read_status(config, parent.task_id)["state"] == "queued"


def test_model_planner_accepts_fenced_json_object():
    parent_task_id = str(uuid4())
    payload = _payload(parent_task_id)
    fenced_text = f"```json\n{json.dumps(payload)}\n```"
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            text_segments=[fenced_text],
        )
    )

    proposal = ModelPlanningAdapter(provider).propose(
        parent_task_id=parent_task_id,
        objective="bounded objective with fenced json",
    )

    assert proposal.parent_task_id == parent_task_id
    assert proposal.proposal_id == "planner-proposal-1"


def test_model_planner_rejects_oversized_response():
    parent_task_id = str(uuid4())
    oversized_text = "x" * (ModelPlanningAdapter._MAX_RESPONSE_CHARS + 1)
    provider = _Provider(
        ModelResponse(
            provider="planner-test",
            model="free-l2-test",
            text_segments=[oversized_text],
        )
    )

    with pytest.raises(PlanningAdapterError, match="planner response exceeds the response limit"):
        ModelPlanningAdapter(provider).propose(
            parent_task_id=parent_task_id,
            objective="bounded objective",
        )


def test_planner_shadow_validates_parent_task_id_before_provider_composition():
    valid = str(uuid4())
    assert validate_parent_task_id(valid) == valid
    with pytest.raises(PlannerShadowInputError, match="parent_task_id must be a UUID string"):
        validate_parent_task_id("not-a-uuid")


def test_planner_pool_selector_keeps_configured_pool_explicit_and_non_secret():
    values = {
        "GEMINI_API_KEY_3": "secret-value",
        "GEMINI_MODEL_3": "gemini-3.6-flash",
    }

    assert resolve_provider_pool(pool_json=None, use_configured_pool=False) is None
    bindings = resolve_provider_pool(
        pool_json=None,
        use_configured_pool=True,
        env=values.get,
    )

    assert bindings is not None
    assert [binding.model for binding in bindings if binding.binding_id == "gemini:worker:free-3"] == ["gemini-3.6-flash"]
    assert all("secret-value" not in repr(binding) for binding in bindings)


def test_planner_pool_selector_rejects_two_pool_sources():
    with pytest.raises(PlannerShadowInputError, match="cannot be combined"):
        resolve_provider_pool(
            pool_json='[{"provider_id":"fake","model":"deterministic"}]',
            use_configured_pool=True,
        )


def test_planner_pool_selector_rejects_non_object_pool_entries():
    with pytest.raises(PlannerShadowInputError, match="invalid binding"):
        resolve_provider_pool(pool_json="[1]", use_configured_pool=False)
