import pytest

from src.dev_agent.domain.protocol import IntelligenceTier, ModelResponse, Task, TaskType
from src.dev_agent.providers.base import ModelProvider
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.providers.factory import ProviderDefinition, ProviderFactory
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import NoRoute, ResourceRouter, RouteRequest
from src.dev_agent.runtime.controller import Controller
from src.dev_agent.state import JsonStateStore
from src.dev_agent.tools import ToolRegistry, ToolRuntime


class RecordingProvider(ModelProvider):
    def __init__(self, provider_id: str, model: str) -> None:
        self.provider_id = provider_id
        self.model = model
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        return ModelResponse(
            provider=self.provider_id,
            model=self.model,
            text_segments=[self.provider_id],
        )


def _tiered_ledger(tmp_path):
    ledger = ResourceLedger(tmp_path / "tiered.sqlite3")
    for resource_id, provider_id, tier in (
        ("l0", "l0-provider", IntelligenceTier.L0.value),
        ("l1", "l1-provider", IntelligenceTier.L1.value),
        ("unknown", "unknown-provider", None),
    ):
        ledger.register_resource(
            resource_id,
            provider_id=provider_id,
            provider_binding_id=provider_id,
            native_unit="request",
            capacity=10,
            capabilities=["text"],
            cost_minor=0,
            intelligence_tier=tier,
        )
        ledger.observe(resource_id, available=10, health="healthy")
    return ledger


def test_router_requires_an_explicit_resource_tier_for_bounded_selection(tmp_path):
    ledger = _tiered_ledger(tmp_path)

    assert ResourceRouter(ledger).choose(
        RouteRequest(capabilities={"text"}, allowed_intelligence_tiers={"L1"})
    ).resource_id == "l1"
    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(
            RouteRequest(capabilities={"text"}, allowed_intelligence_tiers={"L2"})
        )


def test_route_request_rejects_unknown_intelligence_tier():
    with pytest.raises(ValueError, match="allowed_intelligence_tiers"):
        RouteRequest(capabilities={"text"}, allowed_intelligence_tiers={"L9"})


def test_controller_opt_in_uses_policy_tier_to_select_provider_binding(tmp_path):
    ledger = ResourceLedger(tmp_path / "controller-tier.sqlite3")
    for resource_id, provider_id, tier in (
        ("l0", "l0-provider", "L0"),
        ("l1", "l1-provider", "L1"),
    ):
        ledger.register_resource(
            resource_id,
            provider_id=provider_id,
            provider_binding_id=provider_id,
            native_unit="request",
            capacity=10,
            capabilities=["text"],
            cost_minor=0,
            intelligence_tier=tier,
        )
        ledger.observe(resource_id, available=10, health="healthy")
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger))
    low = RecordingProvider("l0-provider", "deterministic")
    worker = RecordingProvider("l1-provider", "cheap-worker")
    dispatcher = ProviderDispatcher(ProviderRegistry([low, worker]), control)

    store = JsonStateStore(tmp_path / "state.json")
    result = Controller(
        dispatcher,
        ToolRuntime(ToolRegistry()),
        store,
        intelligence_routing=True,
    ).run(
        Task(
            objective="bounded worker",
            task_type=TaskType.WORKER,
            metadata={"intelligence_tier": "L3", "selected_tier": "L3"},
        )
    )

    assert result.status.value == "completed"
    assert not low.requests
    assert len(worker.requests) == 1
    assert worker.requests[0].metadata["intelligence_routing"] == "bounded"
    assert worker.requests[0].metadata["allowed_intelligence_tiers"] == ["L1"]


def test_provider_factory_preserves_optional_intelligence_tier_label():
    provider = ProviderFactory().create(
        ProviderDefinition(
            provider_id="openrouter",
            model="openrouter/free",
            provider_binding_id="openrouter:free",
            intelligence_tier=IntelligenceTier.L1.value,
        )
    )

    assert provider.intelligence_tier == "L1"
