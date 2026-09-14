from __future__ import annotations

import json

from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.host_dispatch import HostDispatchEnvelope
from scripts.devfarm_host_dispatch import process_once


class _FakeHostProvider:
    provider_id = "fake"
    provider_binding_id = "fake:host"
    model_id = "fake-model"
    intelligence_tier = "L1"

    def request(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(provider="fake", model="fake-model", text_segments=["host-result"])


def _envelope() -> HostDispatchEnvelope:
    request = ModelRequest(
        task_id="00000000-0000-0000-0000-000000000001",
        messages=[{"role": "user", "content": "bounded"}],
    )
    return HostDispatchEnvelope(
        dispatch_id="dispatch-1",
        provider_id="fake",
        provider_binding_id="fake:host",
        model_id="fake-model",
        intelligence_tier="L1",
        request=request,
        egress_manifest_sha256="c" * 64,
    )


def test_host_runtime_processes_one_request_without_persisting_raw_provider_output(tmp_path):
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(json.dumps(_envelope().to_dict()), encoding="utf-8")

    result = process_once(request_path, response_path, provider_factory=lambda envelope: _FakeHostProvider())

    assert result["status"] == "completed"
    assert result["provider_id"] == "fake"
    response = json.loads(response_path.read_text(encoding="utf-8"))
    assert response["status"] == "completed"
    assert response["response"]["text_segments"] == ["host-result"]
    assert "raw_response" not in response


def test_host_runtime_rejects_unknown_envelope_field_before_provider_factory(tmp_path):
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    payload = _envelope().to_dict()
    payload["endpoint"] = "https://attacker.example"
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    called = False

    def provider_factory(_envelope):
        nonlocal called
        called = True
        return _FakeHostProvider()

    result = process_once(request_path, response_path, provider_factory=provider_factory)

    assert result["status"] == "rejected"
    assert called is False
    assert json.loads(response_path.read_text(encoding="utf-8"))["category"] == "host_configuration"
