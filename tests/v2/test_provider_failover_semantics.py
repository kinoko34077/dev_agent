import io
import json
from urllib.error import HTTPError
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.providers.base import ModelProvider, ProviderError
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderPoolExhausted, ProviderRegistry
from src.dev_agent.providers.gemini import GeminiHttpProvider
from src.dev_agent.operation import OperationProviderBinding
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter
from src.dev_agent.resources.qualification import QualificationProjection, QualificationResolver
from src.dev_agent.resources.billing_catalog import TrustedResourceProfile
import scripts.devfarm_planner_shadow as planner_shadow
from scripts.devfarm_planner_shadow import admit_planner_pool


class _PoolProvider(ModelProvider):
    def __init__(self, provider_id: str, binding_id: str, *, failure: ProviderError | None = None):
        self.provider_id = provider_id
        self.provider_binding_id = binding_id
        self.model_id = f"{provider_id}-model"
        self.model = self.model_id
        self.failure = failure
        self.calls = 0

    def request(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return ModelResponse(provider=self.provider_id, model=self.model_id, text_segments=["ok"], usage={"cost_minor": 0})


def _dispatcher(tmp_path, providers):
    ledger = ResourceLedger(tmp_path / "failover.sqlite3")
    for provider in providers:
        ledger.register_resource(
            provider.provider_binding_id,
            provider_id=provider.provider_id,
            provider_binding_id=provider.provider_binding_id,
            native_unit="request",
            capacity=1,
            capabilities=["text"],
            cost_minor=0,
            metadata={"provider_binding_id": provider.provider_binding_id, "model_id": provider.model_id},
        )
        ledger.observe(provider.provider_binding_id, available=1, health="healthy")
    policy = BudgetPolicy(hard_cap_minor=0, recovery_reserve_minor=0)
    BudgetAuthority.configure(ledger, policy)
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger, policy))
    return ledger, ProviderDispatcher(ProviderRegistry(list(providers)), control)


def _request() -> ModelRequest:
    return ModelRequest(
        task_id="00000000-0000-0000-0000-000000000091",
        messages=[{"role": "user", "content": "bounded planner request"}],
    )


def test_provider_error_separates_retry_from_failover_safety():
    authentication = ProviderError("denied", category="authentication", retryable=False)
    assert authentication.retryable is False
    assert authentication.failover_safe is True
    assert authentication.requires_reconciliation is False

    unknown = ProviderError("connection lost", category="transport", retryable=True)
    assert unknown.retryable is True
    assert unknown.failover_safe is False
    assert unknown.requires_reconciliation is True


def test_dispatcher_failover_safe_authentication_failure_uses_secondary_without_same_binding_retry(tmp_path):
    primary = _PoolProvider("primary", "primary:binding", failure=ProviderError("denied", category="authentication", retryable=False))
    secondary = _PoolProvider("secondary", "secondary:binding")
    ledger, dispatcher = _dispatcher(tmp_path, (primary, secondary))
    try:
        response = dispatcher.request(_request())
        assert response.provider == "secondary"
        assert primary.calls == 1
        assert secondary.calls == 1
    finally:
        ledger.close()


def test_dispatcher_confirmed_unavailable_fails_over_and_does_not_retry_same_binding(tmp_path):
    primary = _PoolProvider(
        "primary",
        "primary:binding",
        failure=ProviderError(
            "provider unavailable",
            category="provider_unavailable",
            retryable=False,
            failover_safe=True,
            http_status=503,
        ),
    )
    secondary = _PoolProvider("secondary", "secondary:binding")
    ledger, dispatcher = _dispatcher(tmp_path, (primary, secondary))
    try:
        response = dispatcher.request(_request())
        assert response.provider == "secondary"
        assert primary.calls == 1
        assert secondary.calls == 1
        assert ledger.get_resource("primary:binding")["consecutive_failures"] == 1
    finally:
        ledger.close()


def test_dispatcher_reports_bounded_pool_exhaustion_after_multiple_safe_failures(tmp_path):
    failures = ProviderError("provider unavailable", category="provider_unavailable", retryable=False, failover_safe=True, http_status=503)
    primary = _PoolProvider("primary", "primary:binding", failure=failures)
    secondary = _PoolProvider(
        "secondary",
        "secondary:binding",
        failure=ProviderError("rate limited", category="rate_limit", retryable=True),
    )
    ledger, dispatcher = _dispatcher(tmp_path, (primary, secondary))
    try:
        with pytest.raises(ProviderPoolExhausted) as raised:
            dispatcher.request(_request())
        assert raised.value.category == "provider_pool_exhausted"
        assert [attempt["binding_id"] for attempt in raised.value.attempts] == ["primary:binding", "secondary:binding"]
        assert all(attempt["failover_safe"] is True for attempt in raised.value.attempts)
        assert all(attempt["reconciliation_required"] is False for attempt in raised.value.attempts)
    finally:
        ledger.close()


def test_gemini_explicit_unavailable_503_is_failover_safe(monkeypatch):
    body = json.dumps({"error": {"status": "UNAVAILABLE", "message": "high demand"}}).encode("utf-8")

    def unavailable(request, timeout):
        raise HTTPError(request.full_url, 503, "Service Unavailable", {}, io.BytesIO(body))

    monkeypatch.setattr("src.dev_agent.providers.gemini.provider.urlopen_no_redirect", unavailable)
    provider = GeminiHttpProvider(model="gemini-3.8-flash", api_key="test-key")
    with pytest.raises(ProviderError) as raised:
        provider.request(_request())
    assert raised.value.category == "provider_unavailable"
    assert raised.value.retryable is False
    assert raised.value.failover_safe is True
    assert raised.value.requires_reconciliation is False


def test_gemini_unclassified_503_remains_unknown_and_cannot_fail_over(monkeypatch):
    body = json.dumps({"error": {"status": "INTERNAL", "message": "temporary internal error"}}).encode("utf-8")

    def internal(request, timeout):
        raise HTTPError(request.full_url, 503, "Service Unavailable", {}, io.BytesIO(body))

    monkeypatch.setattr("src.dev_agent.providers.gemini.provider.urlopen_no_redirect", internal)
    provider = GeminiHttpProvider(model="gemini-3.8-flash", api_key="test-key")
    with pytest.raises(ProviderError) as raised:
        provider.request(_request())
    assert raised.value.category == "provider_http"
    assert raised.value.failover_safe is False
    assert raised.value.requires_reconciliation is True


def test_planner_pool_admission_keeps_exact_l2_and_does_not_downgrade_to_l1():
    resolver = QualificationResolver()
    admitted = admit_planner_pool(
        (
            OperationProviderBinding(
                provider_id="gemini",
                provider_binding_id="gemini:core",
                model="gemini-3.8-flash",
                api_key_env="GEMINI_API_KEY",
                quota_domain="gemini-core-account",
            ),
            OperationProviderBinding(
                provider_id="gemini",
                provider_binding_id="gemini:worker",
                model="gemini-3.5-flash-lite",
                api_key_env="GEMINI_API_KEY",
                quota_domain="gemini-worker-account",
            ),
            OperationProviderBinding(
                provider_id="gemini",
                provider_binding_id="gemini:compat",
                model="gemini-2.5-flash",
                api_key_env="GEMINI_API_KEY",
                quota_domain="gemini-compat-account",
            ),
        ),
        resolver=resolver,
    )

    assert [(binding.binding_id, binding.model) for binding, _qualification, _profile in admitted] == [
        ("gemini:core", "gemini-3.8-flash")
    ]


def test_planner_shadow_pool_fails_over_to_second_exact_l2_and_validates_proposal(tmp_path, monkeypatch):
    parent_task_id = str(uuid4())
    now = datetime.now(timezone.utc)
    qualifications = {
        ("a", "a:planner", "a-model"): QualificationProjection(
            provider_id="a",
            provider_binding_id="a:planner",
            model_id="a-model",
            routing_capabilities=frozenset({"text"}),
            qualification_evidence=frozenset(),
            integration_evidence=frozenset(),
            intelligence_tier="L2",
            tested_at=(now - timedelta(minutes=1)).isoformat(),
            expires_at=(now + timedelta(hours=1)).isoformat(),
            confidence="high",
        ),
        ("b", "b:planner", "b-model"): QualificationProjection(
            provider_id="b",
            provider_binding_id="b:planner",
            model_id="b-model",
            routing_capabilities=frozenset({"text"}),
            qualification_evidence=frozenset(),
            integration_evidence=frozenset(),
            intelligence_tier="L2",
            tested_at=(now - timedelta(minutes=1)).isoformat(),
            expires_at=(now + timedelta(hours=1)).isoformat(),
            confidence="high",
        ),
    }

    class _Resolver:
        def resolve(self, provider_id, binding_id, model_id, *, min_confidence="high"):
            result = qualifications.get((provider_id, binding_id, model_id))
            return result if result is not None and min_confidence == "high" else None

    profiles = {
        identity: TrustedResourceProfile(
            identity[0], identity[1], identity[2], 0, "JPY", True, "L2"
        )
        for identity in qualifications
    }

    class _PlannerProvider(_PoolProvider):
        def __init__(self, provider_id, binding_id, *, failure=None):
            super().__init__(provider_id, binding_id, failure=failure)
            self.model_id = f"{provider_id}-model"
            self.model = self.model_id

        def request(self, request):
            self.calls += 1
            if self.failure is not None:
                raise self.failure
            return ModelResponse(
                provider=self.provider_id,
                model=self.model_id,
                structured_output={
                    "parent_task_id": parent_task_id,
                    "rationale": "use the second admitted planner binding",
                    "planning_cycle": 1,
                    "proposal_id": "pool-proposal-1",
                    "children": [
                        {
                            "child_key": "implementation",
                            "objective": "implement the bounded child",
                            "task_type": "worker",
                            "risk": "low",
                            "sensitivity": "normal",
                            "required_capabilities": ["coding"],
                            "dependencies": [],
                            "dependency_types": {},
                            "suggested_owner": "worker",
                        }
                    ],
                },
                usage={"cost_minor": 0},
            )

    providers = {
        "a:planner": _PlannerProvider(
            "a",
            "a:planner",
            failure=ProviderError(
                "provider unavailable",
                category="provider_unavailable",
                retryable=False,
                failover_safe=True,
                http_status=503,
            ),
        ),
        "b:planner": _PlannerProvider("b", "b:planner"),
    }
    monkeypatch.setattr(planner_shadow, "QualificationResolver", lambda: _Resolver())
    monkeypatch.setattr(planner_shadow, "profile_for", lambda provider, binding, model: profiles.get((provider, binding, model)))
    monkeypatch.setattr("src.dev_agent.resources.router.profile_for", lambda provider, binding, model: profiles.get((provider, binding, model)))
    monkeypatch.setattr(planner_shadow, "_build_provider", lambda *, binding: providers[binding.binding_id])

    result = planner_shadow.run_shadow(
        objective="split a bounded development objective",
        parent_task_id=parent_task_id,
        provider_id="a",
        binding_id="a:planner",
        model_id="a-model",
        api_key_env="A_API_KEY",
        quota_domain="a-quota",
        timeout_seconds=10.0,
        allow_unknown_quota=True,
        repository="example/repo",
        branch="v2/bootstrap",
        provider_pool=(
            planner_shadow.OperationProviderBinding(
                provider_id="a",
                provider_binding_id="a:planner",
                model="a-model",
                api_key_env="A_API_KEY",
                quota_domain="a-quota",
            ),
            planner_shadow.OperationProviderBinding(
                provider_id="b",
                provider_binding_id="b:planner",
                model="b-model",
                api_key_env="B_API_KEY",
                quota_domain="b-quota",
            ),
        ),
    )

    assert result["status"] == "live_shadow_validated"
    assert result["binding"] == "b:planner"
    assert result["host_validation"] == "passed"
    assert [audit["binding"] for audit in result["dispatch_audits"]] == ["a:planner", "b:planner"]
    assert providers["a:planner"].calls == 1
    assert providers["b:planner"].calls == 1
