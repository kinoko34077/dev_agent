from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.operation import OperationService
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderPoolSaturated
from src.dev_agent.providers.fake.provider import FakeProvider
from src.dev_agent.resources.router import NoRoute, RouteSelection
from src.dev_agent.scheduler.queue import DurableQueue


def test_any_provider_capacity_wakes_pool_saturation_waiters(tmp_path):
    queue = DurableQueue(tmp_path / "queue.sqlite3")
    queue.enqueue("pool-waiter")
    item = queue.claim("worker-a", lease_seconds=30)
    queue.defer_for_event(
        item.task_id,
        worker_id="worker-a",
        state_version=item.state_version,
        reason="resource:provider_execution_saturated:pool",
    )

    OperationService._wake_provider_capacity(queue, "gemini:worker")

    assert queue.snapshot("pool-waiter").state == "queued"


def test_dispatcher_preserves_all_saturated_binding_ids(monkeypatch):
    class SaturatedProvider(FakeProvider):
        def request(self, request):
            from src.dev_agent.runtime.model_turn import ProviderExecutionSaturated

            raise ProviderExecutionSaturated("lane saturated", binding_id=self.provider_binding_id)

    first = SaturatedProvider()
    first.provider_id = "gemini"
    first.provider_binding_id = "gemini:worker"
    second = SaturatedProvider()
    second.provider_id = "cloudflare"
    second.provider_binding_id = "cloudflare:worker"

    class Registry:
        def get_binding(self, binding_id):
            return {first.provider_binding_id: first, second.provider_binding_id: second}[binding_id]

    class Control:
        def reserve_selection(self, task_id, selection, *, intent_key=None):
            return SimpleNamespace()

        def release(self, reservation):
            return None

        def mark_dispatching(self, reservation):
            return None

    dispatcher = ProviderDispatcher(Registry(), Control())
    selections = {
        first.provider_binding_id: RouteSelection(
            resource_id="resource-gemini",
            provider_id="gemini",
            native_unit="request",
            estimated_cost_minor=0,
            price_currency="USD",
            provider_binding_id=first.provider_binding_id,
            model_id="worker",
        ),
        second.provider_binding_id: RouteSelection(
            resource_id="resource-cloudflare",
            provider_id="cloudflare",
            native_unit="request",
            estimated_cost_minor=0,
            price_currency="USD",
            provider_binding_id=second.provider_binding_id,
            model_id="worker",
        ),
    }

    def select(request, excluded):
        for binding_id, selection in selections.items():
            if selection.resource_id not in excluded:
                return selection
        raise NoRoute("all eligible bindings excluded")

    monkeypatch.setattr(dispatcher, "_selection", select)
    monkeypatch.setattr(dispatcher, "_prepare_intent", lambda request, selection: None)
    monkeypatch.setattr(dispatcher, "_intent_result", lambda key: None)
    monkeypatch.setattr(dispatcher, "_intent", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatcher, "_record_audit", lambda *args, **kwargs: None)

    with pytest.raises(ProviderPoolSaturated) as exc_info:
        dispatcher.request(
            ModelRequest(
                task_id="00000000-0000-0000-0000-000000000101",
                messages=[{"role": "user", "content": "x"}],
            )
        )

    assert exc_info.value.saturated_binding_ids == ("cloudflare:worker", "gemini:worker")
