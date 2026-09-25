from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ModelResponse, Task, TaskStatus, TaskType
from src.dev_agent.intelligence.planner import RootPlanningValidator
from src.dev_agent.intelligence.planner_adapter import (
    ModelPlanningAdapter,
    ModelPlanningCriticAdapter,
    PLANNING_CRITIC_RESPONSE_SCHEMA,
    PlanningCriticAdapterError,
    PlanningResponseError,
    _planning_failure_spec,
    propose_with_planning_critic,
)
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.operation import OperationProviderBinding
from scripts import devfarm_planner_shadow


class _Provider:
    provider_id = "planner-role-provider"

    def __init__(self, response: ModelResponse | None = None, error: BaseException | None = None):
        self.response = response
        self.error = error
        self.requests: list[ModelRequest] = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _proposal(parent_task_id: str) -> dict[str, object]:
    return {
        "parent_task_id": parent_task_id,
        "rationale": "split the bounded objective into one safe child",
        "planning_cycle": 1,
        "proposal_id": "critic-corrected-proposal",
        "children": [
            {
                "child_key": "implementation",
                "objective": "implement the narrow production change",
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


def _failure(*, response_contract: str = "invalid_json") -> PlanningResponseError:
    return PlanningResponseError(
        "redacted planner contract failure",
        request_id=str(uuid4()),
        provider_response_observed=True,
        response_contract=response_contract,
    )


def test_planning_critic_returns_corrected_proposal_with_fresh_proposal_only_request():
    parent_task_id = str(uuid4())
    planner_failure = _failure()
    provider = _Provider(
        ModelResponse(
            provider="independent-planning-critic",
            model="free-l1-critic",
            structured_output={"corrected_proposal": _proposal(parent_task_id)},
        )
    )

    proposal = ModelPlanningCriticAdapter(provider).correct(
        parent_task_id=parent_task_id,
        objective="split this bounded objective",
        planner_failure=planner_failure,
        context_references={"repository": "kinoko34077/dev_agent", "branch": "v2/bootstrap"},
    )

    assert proposal.parent_task_id == parent_task_id
    assert proposal.children[0].suggested_owner == "worker"
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.request_id != planner_failure.request_id
    assert request.response_schema == PLANNING_CRITIC_RESPONSE_SCHEMA
    assert request.metadata["planning_mode"] == "proposal_only"
    assert request.metadata["planning_refinement"] == "independent_critic"
    assert request.metadata["proposal_only"] is True
    assert request.metadata["integration_authority"] == "host_and_codex"
    assert request.metadata["allowed_intelligence_tiers"] == ["L1"]
    assert request.metadata["critic_of_request_id"] == planner_failure.request_id
    assert "raw response" not in request.messages[0]["content"].lower()
    assert "split this bounded objective" in request.messages[0]["content"]


def test_planner_contract_failure_carries_concrete_task_type_repair_spec():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="planner-role-provider",
            model="local-planner",
            structured_output={
                "parent_task_id": parent_task_id,
                "rationale": "bounded split",
                "children": [{"child_key": "one", "objective": "implement", "task_type": "coding"}],
            },
        )
    )

    with pytest.raises(PlanningResponseError) as caught:
        ModelPlanningAdapter(provider).propose(
            parent_task_id=parent_task_id,
            objective="bounded objective",
        )

    assert caught.value.failure_spec is not None
    assert caught.value.failure_spec.location == "children[].task_type"
    assert "exact task_type enum" in caught.value.failure_spec.required_correction


def test_planner_failure_spec_describes_phase8_continuation_dependencies():
    failure = _planning_failure_spec(
        "continuation dependencies must include both worker-a and worker-b",
        response_contract="invalid_proposal",
    )

    assert failure.location == "children[continuation].dependencies"
    assert failure.expected == '["worker-a", "worker-b"]'
    assert "exactly" in failure.required_correction
    assert "worker-a" in failure.required_correction
    assert "worker-b" in failure.required_correction
    assert "both worker dependencies" in failure.acceptance_checks[0]


def test_planner_failure_spec_describes_phase8_dependency_types():
    failure = _planning_failure_spec(
        "continuation dependency_types must be CODE_INTEGRATED for both workers",
        response_contract="invalid_proposal",
    )

    assert failure.location == "children[continuation].dependency_types"
    assert "CODE_INTEGRATED" in failure.expected
    assert "both worker dependencies" in failure.required_correction


def test_planner_prompt_makes_phase8_continuation_shape_explicit():
    prompt = ModelPlanningAdapter._prompt(
        str(uuid4()),
        "Create exactly two non-overlapping worker children and one continuation that depends on both with CODE_INTEGRATED.",
        "normal",
        {},
        "L1",
    )

    assert "worker-a" in prompt
    assert "worker-b" in prompt
    assert "CODE_INTEGRATED" in prompt
    assert "continuation.dependencies" in prompt


def test_planning_critic_rejects_authority_fields_and_parent_mismatch():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="independent-planning-critic",
            model="free-l1-critic",
            structured_output={
                "corrected_proposal": {**_proposal(str(uuid4())), "approval": True},
            },
        )
    )

    with pytest.raises(PlanningCriticAdapterError, match="unknown"):
        ModelPlanningCriticAdapter(provider).correct(
            parent_task_id=parent_task_id,
            objective="bounded objective",
            planner_failure=_failure(response_contract="invalid_proposal"),
        )


def test_planning_critic_requires_model_output_failure_and_does_not_accept_transport():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="independent-planning-critic",
            model="free-l1-critic",
            structured_output={"corrected_proposal": _proposal(parent_task_id)},
        )
    )

    with pytest.raises(PlanningCriticAdapterError, match="response-contract"):
        ModelPlanningCriticAdapter(provider).correct(
            parent_task_id=parent_task_id,
            objective="bounded objective",
            planner_failure=ProviderError("transport", category="transport"),  # type: ignore[arg-type]
        )
    assert provider.requests == []


def test_planning_critic_correlates_provider_failure_to_its_fresh_request():
    parent_task_id = str(uuid4())
    failure = ProviderError("transport", category="transport")
    provider = _Provider(error=failure)

    with pytest.raises(ProviderError):
        ModelPlanningCriticAdapter(provider).correct(
            parent_task_id=parent_task_id,
            objective="bounded objective",
            planner_failure=_failure(),
        )

    assert len(provider.requests) == 1
    assert failure.request_id == provider.requests[0].request_id


def test_planning_critic_contract_failure_preserves_its_fresh_request_id():
    parent_task_id = str(uuid4())
    provider = _Provider(
        ModelResponse(
            provider="independent-planning-critic",
            model="free-l1-critic",
            text_segments=["not-json"],
        )
    )

    with pytest.raises(PlanningCriticAdapterError) as caught:
        ModelPlanningCriticAdapter(provider).correct(
            parent_task_id=parent_task_id,
            objective="bounded objective",
            planner_failure=_failure(),
        )

    assert len(provider.requests) == 1
    assert caught.value.request_id == provider.requests[0].request_id


def test_planning_critic_is_available_through_lazy_intelligence_exports():
    from src.dev_agent import intelligence

    assert intelligence.ModelPlanningCriticAdapter is ModelPlanningCriticAdapter
    assert intelligence.PLANNING_CRITIC_RESPONSE_SCHEMA is PLANNING_CRITIC_RESPONSE_SCHEMA
    assert intelligence.PlanningCriticAdapterError is PlanningCriticAdapterError


def test_planner_composition_uses_one_critic_action_then_host_validation():
    parent_task_id = str(uuid4())
    planner_provider = _Provider(
        ModelResponse(
            provider="planner",
            model="free-l2-planner",
            text_segments=["not-json"],
        )
    )
    critic_provider = _Provider(
        ModelResponse(
            provider="independent-planning-critic",
            model="free-l1-critic",
            structured_output={"corrected_proposal": _proposal(parent_task_id)},
        )
    )
    parent = Task(
        task_id=parent_task_id,
        objective="split this bounded objective",
        status=TaskStatus.READY,
        task_type=TaskType.REASONING,
    )
    validated: list[object] = []

    def host_validate(proposal):
        accepted = RootPlanningValidator.validate(parent, proposal)
        validated.append(accepted)
        return accepted

    proposal = propose_with_planning_critic(
        ModelPlanningAdapter(planner_provider),
        ModelPlanningCriticAdapter(critic_provider),
        parent_task_id=parent_task_id,
        objective=parent.objective,
        host_validate=host_validate,
    )

    assert proposal.parent_task_id == parent_task_id
    assert len(planner_provider.requests) == 1
    assert len(critic_provider.requests) == 1
    assert len(validated) == 1
    critic_prompt = critic_provider.requests[0].messages[0]["content"]
    assert "CONCRETE FAILURE SPEC" in critic_prompt
    assert "REPAIR DIRECTIVE" in critic_prompt


def test_planner_composition_does_not_invoke_critic_for_provider_failure():
    parent_task_id = str(uuid4())
    planner_provider = _Provider(error=ProviderError("transport", category="transport"))
    critic_provider = _Provider(
        ModelResponse(
            provider="independent-planning-critic",
            model="free-l1-critic",
            structured_output={"corrected_proposal": _proposal(parent_task_id)},
        )
    )

    with pytest.raises(ProviderError):
        propose_with_planning_critic(
            ModelPlanningAdapter(planner_provider),
            ModelPlanningCriticAdapter(critic_provider),
            parent_task_id=parent_task_id,
            objective="bounded objective",
        )

    assert critic_provider.requests == []


def test_planner_shadow_uses_separate_admitted_critic_pool(monkeypatch):
    parent_task_id = str(uuid4())
    planner_binding = OperationProviderBinding(
        provider_id="planner",
        model="free-l2-planner",
        provider_binding_id="planner-binding",
        quota_domain="planner-quota",
        api_key_env="PLANNER_KEY",
    )
    critic_binding = OperationProviderBinding(
        provider_id="critic",
        model="free-l1-critic",
        provider_binding_id="critic-binding",
        quota_domain="critic-quota",
        api_key_env="CRITIC_KEY",
    )

    planner_provider = _Provider(
        ModelResponse(
            provider="planner",
            model="free-l2-planner",
            text_segments=["not-json"],
        )
    )
    critic_provider = _Provider(
        ModelResponse(
            provider="critic",
            model="free-l1-critic",
            structured_output={"corrected_proposal": _proposal(parent_task_id)},
        )
    )
    admitted_tiers: list[str] = []

    def fake_admit(bindings, *, required_tier="L2", **_kwargs):
        admitted_tiers.append(required_tier)
        if required_tier == "L1":
            return ((critic_binding, SimpleNamespace(intelligence_tier="L1"), SimpleNamespace()),)
        return ((planner_binding, SimpleNamespace(intelligence_tier="L2"), SimpleNamespace()),)

    @contextmanager
    def fake_compose(admitted, *, resource_id_prefix, **_kwargs):
        provider = planner_provider if resource_id_prefix == "planner-shadow" else critic_provider
        provider.audits = []
        yield SimpleNamespace(
            ledger=SimpleNamespace(list_quota_observations=lambda **_query: []),
            dispatcher=provider,
            admitted=tuple(admitted),
            directory=Path("."),
        )

    monkeypatch.setattr(devfarm_planner_shadow, "admit_planner_pool", fake_admit)
    monkeypatch.setattr(devfarm_planner_shadow, "compose_resource_pool", fake_compose)

    result = devfarm_planner_shadow.run_shadow(
        objective="split this bounded objective",
        parent_task_id=parent_task_id,
        provider_id="planner",
        binding_id="planner-binding",
        model_id="free-l2-planner",
        api_key_env="PLANNER_KEY",
        quota_domain="planner-quota",
        timeout_seconds=1.0,
        allow_unknown_quota=False,
        repository="kinoko34077/dev_agent",
        branch="v2/bootstrap",
        provider_pool=(planner_binding,),
        planning_critic_pool=(critic_binding,),
        execution_boundary="in_process",
    )

    assert result["status"] == "live_shadow_validated"
    assert result["convergence"]["completed"] is True
    assert result["convergence"]["refinement_rounds"] == 1
    assert result["convergence"]["attempts"][-1]["convergence_state"] == "COMPLETED"
    assert admitted_tiers == ["L2", "L1"]
    assert len(planner_provider.requests) == 1
    assert len(critic_provider.requests) == 1
    assert critic_provider.requests[0].metadata["planning_refinement"] == "independent_critic"


def test_planner_shadow_fast_path_skips_configured_critic(monkeypatch):
    parent_task_id = str(uuid4())
    planner_binding = OperationProviderBinding(
        provider_id="planner",
        model="free-l2-planner",
        provider_binding_id="planner-binding",
        quota_domain="planner-quota",
        api_key_env="PLANNER_KEY",
    )
    critic_binding = OperationProviderBinding(
        provider_id="critic",
        model="free-l1-critic",
        provider_binding_id="critic-binding",
        quota_domain="critic-quota",
        api_key_env="CRITIC_KEY",
    )
    planner_provider = _Provider(
        ModelResponse(
            provider="planner",
            model="free-l2-planner",
            structured_output=_proposal(parent_task_id),
        )
    )
    critic_provider = _Provider(
        ModelResponse(
            provider="critic",
            model="free-l1-critic",
            structured_output={"corrected_proposal": _proposal(parent_task_id)},
        )
    )

    def fake_admit(bindings, *, required_tier="L2", **_kwargs):
        if required_tier == "L1":
            return ((critic_binding, SimpleNamespace(intelligence_tier="L1"), SimpleNamespace()),)
        return ((planner_binding, SimpleNamespace(intelligence_tier="L2"), SimpleNamespace()),)

    @contextmanager
    def fake_compose(admitted, *, resource_id_prefix, **_kwargs):
        provider = planner_provider if resource_id_prefix == "planner-shadow" else critic_provider
        provider.audits = []
        yield SimpleNamespace(
            ledger=SimpleNamespace(list_quota_observations=lambda **_query: []),
            dispatcher=provider,
            admitted=tuple(admitted),
            directory=Path("."),
        )

    monkeypatch.setattr(devfarm_planner_shadow, "admit_planner_pool", fake_admit)
    monkeypatch.setattr(devfarm_planner_shadow, "compose_resource_pool", fake_compose)

    result = devfarm_planner_shadow.run_shadow(
        objective="split this bounded objective",
        parent_task_id=parent_task_id,
        provider_id="planner",
        binding_id="planner-binding",
        model_id="free-l2-planner",
        api_key_env="PLANNER_KEY",
        quota_domain="planner-quota",
        timeout_seconds=1.0,
        allow_unknown_quota=False,
        repository="kinoko34077/dev_agent",
        branch="v2/bootstrap",
        provider_pool=(planner_binding,),
        planning_critic_pool=(critic_binding,),
        execution_boundary="in_process",
    )

    assert result["status"] == "live_shadow_validated"
    assert result["convergence"]["completed"] is True
    assert result["convergence"]["refinement_rounds"] == 0
    assert result["convergence"]["attempts"][0]["convergence_state"] == "FAST_PATH"
    assert result["planning_critic"] == {
        "configured": True,
        "invoked": False,
        "request_id": None,
        "dispatch_audits": [],
    }
    assert len(planner_provider.requests) == 1
    assert critic_provider.requests == []


def test_planner_shadow_projects_critic_invocation_as_bounded_observation(monkeypatch):
    parent_task_id = str(uuid4())
    planner_binding = OperationProviderBinding(
        provider_id="planner",
        model="free-l2-planner",
        provider_binding_id="planner-binding",
        quota_domain="planner-quota",
        api_key_env="PLANNER_KEY",
    )
    critic_binding = OperationProviderBinding(
        provider_id="critic",
        model="free-l1-critic",
        provider_binding_id="critic-binding",
        quota_domain="critic-quota",
        api_key_env="CRITIC_KEY",
    )
    planner_provider = _Provider(
        ModelResponse(provider="planner", model="free-l2-planner", text_segments=["not-json"])
    )
    critic_provider = _Provider(
        ModelResponse(
            provider="critic",
            model="free-l1-critic",
            structured_output={"corrected_proposal": _proposal(parent_task_id)},
        )
    )

    def fake_admit(bindings, *, required_tier="L2", **_kwargs):
        if required_tier == "L1":
            return ((critic_binding, SimpleNamespace(intelligence_tier="L1"), SimpleNamespace()),)
        return ((planner_binding, SimpleNamespace(intelligence_tier="L2"), SimpleNamespace()),)

    @contextmanager
    def fake_compose(admitted, *, resource_id_prefix, **_kwargs):
        provider = planner_provider if resource_id_prefix == "planner-shadow" else critic_provider
        provider.audits = []
        yield SimpleNamespace(
            ledger=SimpleNamespace(list_quota_observations=lambda **_query: []),
            dispatcher=provider,
            admitted=tuple(admitted),
            directory=Path("."),
        )

    monkeypatch.setattr(devfarm_planner_shadow, "admit_planner_pool", fake_admit)
    monkeypatch.setattr(devfarm_planner_shadow, "compose_resource_pool", fake_compose)

    result = devfarm_planner_shadow.run_shadow(
        objective="split this bounded objective",
        parent_task_id=parent_task_id,
        provider_id="planner",
        binding_id="planner-binding",
        model_id="free-l2-planner",
        api_key_env="PLANNER_KEY",
        quota_domain="planner-quota",
        timeout_seconds=1.0,
        allow_unknown_quota=False,
        repository="kinoko34077/dev_agent",
        branch="v2/bootstrap",
        provider_pool=(planner_binding,),
        planning_critic_pool=(critic_binding,),
        execution_boundary="in_process",
    )

    observation = result["planning_critic"]
    assert observation["configured"] is True
    assert observation["invoked"] is True
    assert observation["request_id"] == critic_provider.requests[0].request_id
    assert observation["request_id"] != planner_provider.requests[0].request_id


def test_planner_shadow_projects_critic_failure_without_unbound_exception(monkeypatch, capsys):
    request_id = str(uuid4())
    failure = PlanningCriticAdapterError("bounded critic response failure")
    failure.request_id = request_id

    monkeypatch.setattr(
        devfarm_planner_shadow.ModelEvidenceCatalog,
        "load_default",
        lambda: SimpleNamespace(resolver=None, catalog=None),
    )
    monkeypatch.setattr(
        devfarm_planner_shadow,
        "resolve_provider_pool",
        lambda **_kwargs: None,
    )

    def fail_shadow(**_kwargs):
        raise failure

    monkeypatch.setattr(devfarm_planner_shadow, "run_shadow", fail_shadow)

    code = devfarm_planner_shadow.main(
        [
            "--objective",
            "bounded planner correction",
            "--parent-task-id",
            str(uuid4()),
        ]
    )

    assert code == 2
    output = __import__("json").loads(capsys.readouterr().out)
    assert output["status"] == "failed"
    assert output["category"] == "model_output_invalid"
    assert output["request_id"] == request_id
    assert output["reconciliation_required"] is False


def test_planner_shadow_projects_critic_observation_on_failure(monkeypatch, capsys):
    critic_request_id = str(uuid4())
    failure = PlanningCriticAdapterError("bounded critic response failure")
    failure.request_id = critic_request_id
    failure.planning_critic_observation = {
        "configured": True,
        "invoked": True,
        "request_id": critic_request_id,
        "dispatch_audits": [],
    }

    monkeypatch.setattr(
        devfarm_planner_shadow.ModelEvidenceCatalog,
        "load_default",
        lambda: SimpleNamespace(resolver=None, catalog=None),
    )
    monkeypatch.setattr(
        devfarm_planner_shadow,
        "resolve_provider_pool",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(devfarm_planner_shadow, "run_shadow", lambda **_kwargs: (_ for _ in ()).throw(failure))

    code = devfarm_planner_shadow.main(
        [
            "--objective",
            "bounded planner correction",
            "--parent-task-id",
            str(uuid4()),
        ]
    )

    assert code == 2
    output = __import__("json").loads(capsys.readouterr().out)
    assert output["planning_critic"] == failure.planning_critic_observation


def test_planner_shadow_attaches_unused_critic_observation_to_transport_failure(monkeypatch):
    parent_task_id = str(uuid4())
    planner_binding = OperationProviderBinding(
        provider_id="planner",
        model="free-l2-planner",
        provider_binding_id="planner-binding",
        quota_domain="planner-quota",
        api_key_env="PLANNER_KEY",
    )
    critic_binding = OperationProviderBinding(
        provider_id="critic",
        model="free-l1-critic",
        provider_binding_id="critic-binding",
        quota_domain="critic-quota",
        api_key_env="CRITIC_KEY",
    )
    transport_failure = ProviderError("transport", category="transport")
    planner_provider = _Provider(error=transport_failure)
    critic_provider = _Provider(
        ModelResponse(
            provider="critic",
            model="free-l1-critic",
            structured_output={"corrected_proposal": _proposal(parent_task_id)},
        )
    )

    def fake_admit(bindings, *, required_tier="L2", **_kwargs):
        if required_tier == "L1":
            return ((critic_binding, SimpleNamespace(intelligence_tier="L1"), SimpleNamespace()),)
        return ((planner_binding, SimpleNamespace(intelligence_tier="L2"), SimpleNamespace()),)

    @contextmanager
    def fake_compose(admitted, *, resource_id_prefix, **_kwargs):
        provider = planner_provider if resource_id_prefix == "planner-shadow" else critic_provider
        provider.audits = []
        yield SimpleNamespace(
            ledger=SimpleNamespace(list_quota_observations=lambda **_query: []),
            dispatcher=provider,
            admitted=tuple(admitted),
            directory=Path("."),
        )

    monkeypatch.setattr(devfarm_planner_shadow, "admit_planner_pool", fake_admit)
    monkeypatch.setattr(devfarm_planner_shadow, "compose_resource_pool", fake_compose)

    with pytest.raises(ProviderError) as caught:
        devfarm_planner_shadow.run_shadow(
            objective="split this bounded objective",
            parent_task_id=parent_task_id,
            provider_id="planner",
            binding_id="planner-binding",
            model_id="free-l2-planner",
            api_key_env="PLANNER_KEY",
            quota_domain="planner-quota",
            timeout_seconds=1.0,
            allow_unknown_quota=False,
            repository="kinoko34077/dev_agent",
            branch="v2/bootstrap",
            provider_pool=(planner_binding,),
            planning_critic_pool=(critic_binding,),
            execution_boundary="in_process",
        )

    assert caught.value.requires_reconciliation is True
    assert caught.value.planning_critic_observation == {
        "configured": True,
        "invoked": False,
        "request_id": None,
        "dispatch_audits": [],
    }
    assert planner_provider.requests
    assert critic_provider.requests == []
