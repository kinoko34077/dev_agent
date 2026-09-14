from __future__ import annotations

import pytest
import sys

from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.base import ProviderError, TransportFailureCategory
from src.dev_agent.providers.host_dispatch import HostDispatchEnvelope, HostProcessExecutor, HostProviderDispatch


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


def test_host_dispatch_envelope_contains_only_bounded_identity_and_request():
    provider = _Provider(ModelResponse(provider="fake", model="fake-model", text_segments=["ok"]))
    dispatch = HostProviderDispatch(provider, execution_boundary="host_process")

    envelope = dispatch.envelope(_request(), egress_manifest_sha256="a" * 64)
    encoded = envelope.to_dict()

    assert encoded["provider_id"] == "fake"
    assert encoded["provider_binding_id"] == "fake:host"
    assert encoded["egress_manifest_sha256"] == "a" * 64
    assert "api_key" not in encoded
    assert "api_key_env" not in encoded
    assert "base_url" not in encoded
    assert HostDispatchEnvelope.from_dict(encoded) == envelope


@pytest.mark.parametrize("forbidden", ["api_key", "authorization", "base_url", "credential_id"])
def test_host_dispatch_envelope_rejects_credential_or_endpoint_fields(forbidden):
    provider = _Provider(ModelResponse(provider="fake", model="fake-model", text_segments=["ok"]))
    envelope = HostProviderDispatch(provider).envelope(_request(), egress_manifest_sha256="b" * 64).to_dict()
    envelope[forbidden] = "must-not-cross-boundary"

    with pytest.raises(ValueError, match="unknown Host dispatch field"):
        HostDispatchEnvelope.from_dict(envelope)


def test_host_process_executor_treats_missing_response_as_unknown_without_retry(tmp_path):
    provider = _Provider(ModelResponse(provider="fake", model="fake-model", text_segments=["unused"]))
    request = _request()
    request.metadata["egress_manifest_sha256"] = "d" * 64
    executor = HostProcessExecutor((sys.executable, "-c", "pass"), request_dir=tmp_path, timeout_seconds=2)

    with pytest.raises(ProviderError) as caught:
        executor(provider, request)

    assert caught.value.category == "reconciliation_required"
    assert provider.calls == 0
    assert list(tmp_path.iterdir()) == []
