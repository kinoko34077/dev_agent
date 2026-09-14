from __future__ import annotations

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.base import ProviderError, TransportFailureCategory
from src.dev_agent.providers.host_dispatch import HostProviderDispatch


class _Provider:
    provider_id = "fake"
    provider_binding_id = "fake:host"
    model_id = "fake-model"
    intelligence_tier = "L1"

    def __init__(self, response: ModelResponse | None = None, error: BaseException | None = None) -> None:
        self.response = response
        self.error = error
        self.calls = 0

    def request(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def _request() -> ModelRequest:
    return ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[])


def test_host_dispatch_executes_one_provider_call_without_owning_retry_or_routing():
    provider = _Provider(ModelResponse(provider="fake", model="fake-model", text_segments=["ok"]))
    dispatch = HostProviderDispatch(provider)

    response = dispatch.request(_request())

    assert response.text_segments == ["ok"]
    assert provider.calls == 1
    assert dispatch.execution_boundary == "unclassified"
    assert dispatch.provider_identity == {
        "provider_id": "fake",
        "provider_binding_id": "fake:host",
        "model_id": "fake-model",
        "intelligence_tier": "L1",
    }


def test_host_dispatch_preserves_unknown_provider_effect_and_exposes_bounded_diagnostic():
    error = ProviderError("provider transport failed", category="transport", retryable=True)
    error.__cause__ = OSError(10013, "permission denied")
    provider = _Provider(error=error)
    dispatch = HostProviderDispatch(provider, execution_boundary="codex_sandbox")

    with pytest.raises(ProviderError) as caught:
        dispatch.request(_request())

    assert caught.value.requires_reconciliation is True
    assert dispatch.last_transport_category is TransportFailureCategory.SANDBOX_NETWORK_DENIED
    assert provider.calls == 1


def test_host_dispatch_rejects_unsupported_execution_boundary():
    provider = _Provider(ModelResponse(provider="fake", model="fake-model", text_segments=["ok"]))

    with pytest.raises(ValueError, match="execution_boundary"):
        HostProviderDispatch(provider, execution_boundary="arbitrary_proxy")


def test_host_dispatch_can_receive_a_host_runtime_executor_without_adding_retry():
    provider = _Provider(ModelResponse(provider="fake", model="fake-model", text_segments=["inline"]))
    calls = []

    def execute(selected_provider, request):
        calls.append((selected_provider, request.request_id))
        return ModelResponse(provider="fake", model="fake-model", text_segments=["host"])

    response = HostProviderDispatch(provider, execution_boundary="host_process", executor=execute).request(_request())

    assert response.text_segments == ["host"]
    assert len(calls) == 1
    assert provider.calls == 0
