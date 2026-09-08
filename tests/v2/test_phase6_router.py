import pytest

from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import NoRoute, ResourceRouter, RouteRequest
from src.dev_agent.resources.survival import SurvivalGovernor, SurvivalMode, SurvivalSnapshot


def _ledger(tmp_path):
    ledger = ResourceLedger(tmp_path / "router.sqlite3")
    ledger.register_resource("private", provider_id="ollama", native_unit="request", capacity=10, capabilities=["text", "tool_call"], sensitivity="sensitive", cost_minor=0)
    ledger.register_resource("cloud", provider_id="gemini", native_unit="request", capacity=10, capabilities=["text", "tool_call"], sensitivity="internal", cost_minor=20)
    ledger.observe("private", available=8, health="healthy")
    ledger.observe("cloud", available=8, health="healthy")
    return ledger


def test_router_prioritizes_privacy_over_cost_and_filters_capabilities(tmp_path):
    ledger = _ledger(tmp_path)
    router = ResourceRouter(ledger)
    selection = router.choose(RouteRequest(capabilities={"tool_call"}, sensitivity="sensitive", max_cost_minor=100))
    assert selection.resource_id == "private"
    assert selection.provider_id == "ollama"


def test_router_excludes_open_circuit_and_reports_no_route(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.record_provider_failure("ollama", threshold=1, cooldown_seconds=60)
    router = ResourceRouter(ledger)
    with pytest.raises(NoRoute, match="no eligible resource"):
        router.choose(RouteRequest(capabilities={"tool_call"}, sensitivity="sensitive", max_cost_minor=100))


def test_survival_mode_is_deterministic_and_not_model_selected():
    governor = SurvivalGovernor()
    assert governor.evaluate(SurvivalSnapshot(normal_remaining_minor=100, recovery_remaining_minor=20, healthy_resources=2)).mode is SurvivalMode.NORMAL
    assert governor.evaluate(SurvivalSnapshot(normal_remaining_minor=10, recovery_remaining_minor=20, healthy_resources=1)).mode is SurvivalMode.CONSERVE
    assert governor.evaluate(SurvivalSnapshot(normal_remaining_minor=0, recovery_remaining_minor=20, healthy_resources=0)).mode is SurvivalMode.SURVIVAL
def test_router_rejects_stale_observation_when_max_age_is_set(tmp_path):
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")
    ledger.register_resource("old", provider_id="old", native_unit="request", capacity=1, capabilities=["text"], cost_minor=0)
    ledger.observe("old", available=1, health="healthy", observed_at="2020-01-01T00:00:00+00:00")
    with pytest.raises(NoRoute, match="no eligible resource"):
        ResourceRouter(ledger).choose(RouteRequest(capabilities={"text"}, max_observation_age_seconds=60))
