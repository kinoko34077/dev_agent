from __future__ import annotations

import json
from contextlib import contextmanager
import socket
from types import SimpleNamespace

from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.operation import OperationProviderBinding
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.host_dispatch import HostDispatchEnvelope
from src.dev_agent.resources.control import DispatchDenied
import scripts.devfarm_host_dispatch as host_dispatch
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


def test_host_runtime_projects_unexpected_failure_as_bounded_reconciliation(tmp_path):
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(json.dumps(_envelope().to_dict()), encoding="utf-8")

    def provider_factory(_envelope):
        raise RuntimeError("private diagnostic must not cross the boundary")

    result = process_once(request_path, response_path, provider_factory=provider_factory)

    assert result == {
        "status": "failed",
        "category": "host_runtime_failure",
        "reconciliation_required": True,
        "exception_type": "RuntimeError",
    }
    assert json.loads(response_path.read_text(encoding="utf-8")) == result


def test_host_runtime_projects_safe_transport_diagnostics_without_exception_text(tmp_path):
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(json.dumps(_envelope().to_dict()), encoding="utf-8")

    class _DnsFailureProvider(_FakeHostProvider):
        def request(self, request: ModelRequest) -> ModelResponse:
            failure = ProviderError("provider transport failed: private resolver detail", category="transport", retryable=True)
            failure.__cause__ = socket.gaierror(-2, "private resolver detail")
            raise failure

    result = process_once(request_path, response_path, provider_factory=lambda envelope: _DnsFailureProvider())

    assert result["status"] == "failed"
    assert result["category"] == "transport"
    assert result["reconciliation_required"] is True
    assert result["transport_failure_category"] == "dns_failure"
    assert result["transport_stage"] == "resolve"
    assert result["transport_exception_type"] == "gaierror"
    assert "private resolver detail" not in str({key: result.get(key) for key in result if key.startswith("transport_")})
    assert json.loads(response_path.read_text(encoding="utf-8")) == result


def test_host_runtime_preserves_local_dispatch_denial_without_falsely_marking_unknown(tmp_path, monkeypatch):
    request_path = tmp_path / "request.json"
    response_path = tmp_path / "response.json"
    request_path.write_text(json.dumps(_envelope().to_dict()), encoding="utf-8")

    def deny_before_external_effect(_envelope):
        raise DispatchDenied("no_route", "no eligible resource")

    monkeypatch.setattr(host_dispatch, "_dispatch_configured", deny_before_external_effect)

    result = process_once(request_path, response_path)

    assert result == {
        "status": "failed",
        "category": "no_route",
        "retryable": False,
        "failover_safe": False,
        "reconciliation_required": False,
        "http_status": None,
    }
    assert json.loads(response_path.read_text(encoding="utf-8")) == result


def test_host_runtime_resolves_materialized_discovered_model_from_configured_lane(monkeypatch):
    configured = OperationProviderBinding(
        provider_id="gemini",
        model="gemini-3.8-flash",
        provider_binding_id="gemini:worker:free-3",
        qualification_binding_id="gemini:worker:free-3",
        quota_domain="gemini:project:982142111392",
        api_key_env="GEMINI_API_KEY_3",
    )
    expanded = OperationProviderBinding(
        provider_id="gemini",
        model="gemini-3.6-flash",
        provider_binding_id="gemini:worker:free-3::model::gemini-3.6-flash::digest",
        qualification_binding_id="gemini:worker:free-3",
        quota_domain="gemini:project:982142111392",
        api_key_env="GEMINI_API_KEY_3",
    )
    monkeypatch.setattr(host_dispatch, "configured_provider_pool_from_environment", lambda: (configured,))
    monkeypatch.setattr(
        host_dispatch,
        "materialize_provider_bindings",
        lambda binding, _catalog, expand_discovered_models: (binding, expanded)
        if expand_discovered_models
        else (binding,),
    )
    monkeypatch.setattr(
        host_dispatch,
        "ModelEvidenceCatalog",
        SimpleNamespace(load_default=lambda: SimpleNamespace(catalog=object())),
    )
    envelope = HostDispatchEnvelope(
        dispatch_id="dispatch-expanded",
        provider_id="gemini",
        provider_binding_id=expanded.binding_id,
        model_id=expanded.model,
        intelligence_tier="L2",
        request=ModelRequest(
            task_id="00000000-0000-0000-0000-000000000001",
            messages=[{"role": "user", "content": "bounded"}],
        ),
        egress_manifest_sha256="d" * 64,
    )

    assert host_dispatch._configured_binding(envelope) == expanded


def test_host_runtime_resolves_expanded_model_from_credential_lane_identity(monkeypatch):
    configured = OperationProviderBinding(
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        provider_binding_id="gemini:worker:free-3",
        qualification_binding_id="gemini:worker:free-3",
        quota_domain="gemini:project:982142111392",
        api_key_env="GEMINI_API_KEY_3",
    )
    expanded = OperationProviderBinding(
        provider_id="gemini",
        model="gemini-3.5-flash-lite",
        provider_binding_id="gemini:worker:free-3::model::gemini-3.5-flash-lite::digest",
        qualification_binding_id="gemini:worker:free-3",
        quota_domain="gemini:project:982142111392",
        api_key_env="GEMINI_API_KEY_3",
    )
    monkeypatch.setattr(host_dispatch, "configured_provider_pool_from_environment", lambda: (configured,))
    monkeypatch.setattr(
        host_dispatch,
        "materialize_provider_bindings",
        lambda binding, _catalog, expand_discovered_models: (expanded,)
        if expand_discovered_models
        else (binding,),
    )
    monkeypatch.setattr(
        host_dispatch,
        "ModelEvidenceCatalog",
        SimpleNamespace(load_default=lambda: SimpleNamespace(catalog=object())),
    )
    envelope = HostDispatchEnvelope(
        dispatch_id="dispatch-credential-lane",
        provider_id="gemini",
        provider_binding_id="gemini:worker:free-3",
        model_id=expanded.model,
        intelligence_tier="L1",
        request=ModelRequest(
            task_id="00000000-0000-0000-0000-000000000003",
            messages=[{"role": "user", "content": "bounded"}],
        ),
        egress_manifest_sha256="f" * 64,
    )

    assert host_dispatch._configured_binding(envelope) == expanded


def test_host_dispatch_propagates_model_admission_into_runtime_composition(monkeypatch):
    binding = OperationProviderBinding(
        provider_id="gemini",
        model="gemini-3.6-flash",
        provider_binding_id="gemini:worker:free-3::model::gemini-3.6-flash::digest",
        qualification_binding_id="gemini:worker:free-3",
        quota_domain="gemini:project:982142111392",
        api_key_env="GEMINI_API_KEY_3",
    )
    evidence = SimpleNamespace(catalog=object(), resolver=object())
    admitted = ((binding, SimpleNamespace(), SimpleNamespace()),)
    monkeypatch.setattr(host_dispatch, "_configured_binding", lambda _envelope: binding)
    monkeypatch.setattr(host_dispatch, "ModelEvidenceCatalog", SimpleNamespace(load_default=lambda: evidence))
    monkeypatch.setattr(host_dispatch, "admit_resource_pool", lambda *args, **kwargs: admitted)

    class _Dispatcher:
        provider_id = "resource-router"

        def request(self, _request):
            return ModelResponse(provider="gemini", model="gemini-3.6-flash", text_segments=["{}"])

    @contextmanager
    def fake_compose(_admitted, *, resolver, model_admission_resolver, resource_id_prefix):
        assert model_admission_resolver is evidence.resolver
        assert resource_id_prefix == "devfarm-host"
        yield SimpleNamespace(dispatcher=_Dispatcher())

    monkeypatch.setattr(host_dispatch, "compose_resource_pool", fake_compose)
    envelope = HostDispatchEnvelope(
        dispatch_id="dispatch-admission",
        provider_id="gemini",
        provider_binding_id=binding.binding_id,
        model_id=binding.model,
        intelligence_tier="L2",
        request=ModelRequest(
            task_id="00000000-0000-0000-0000-000000000002",
            messages=[{"role": "user", "content": "bounded"}],
            requested_capabilities=["text"],
            metadata={"allowed_intelligence_tiers": ["L2"]},
        ),
        egress_manifest_sha256="e" * 64,
    )

    response = host_dispatch._dispatch_configured(envelope)

    assert response.provider == "gemini"
    assert response.model == "gemini-3.6-flash"
