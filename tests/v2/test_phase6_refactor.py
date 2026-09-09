from src.dev_agent.domain.protocol import Task
from src.dev_agent.resources.catalog import ResourceCatalogStore
from src.dev_agent.resources.observations import QuotaObservationStore, ResourceObservationStore
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.runtime import RuntimeState


def test_runtime_state_round_trips_checkpoint_json_without_aliasing():
    state = RuntimeState.initial(Task(objective="typed state"), now=100.0)
    state["next_step_order"] = 2
    state["pending_tool_calls"].append({"call_id": "call"})

    checkpoint = state.to_checkpoint()
    checkpoint["pending_tool_calls"].append({"call_id": "external"})
    restored = RuntimeState.from_checkpoint(state.to_checkpoint())

    assert restored.deadline_epoch == 100.0 + 300.0
    assert restored["next_step_order"] == 2
    assert restored.pending_tool_calls == [{"call_id": "call"}]
    assert state.pending_tool_calls == [{"call_id": "call"}]
    assert restored.active_request_id is None


def test_resource_ledger_uses_internal_catalog_observation_and_quota_stores(tmp_path):
    ledger = ResourceLedger(tmp_path / "resources.sqlite3")

    assert isinstance(ledger._catalog_store, ResourceCatalogStore)
    assert isinstance(ledger._observation_store, ResourceObservationStore)
    assert isinstance(ledger._quota_store, QuotaObservationStore)

    ledger.register_resource(
        "cloud-free",
        provider_id="cloudflare",
        native_unit="request",
        capacity=2,
        capabilities=["text"],
        quota_domain="cloud-account",
    )
    ledger.observe("cloud-free", available=2, health="healthy")
    ledger.observe_quota("cloud-free", unit="requests", limit=10, remaining=9, source="test")

    assert ledger.get_resource("cloud-free")["provider_id"] == "cloudflare"
    assert ledger.get_quota_observation("cloud-free")["remaining"] == 9
