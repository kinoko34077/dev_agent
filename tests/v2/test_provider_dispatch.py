from src.dev_agent.domain.protocol import ModelRequest, ModelResponse, Task, TaskStatus
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.dispatch import ProviderDispatcher, ProviderRegistry
from src.dev_agent.providers.journal import ProviderDispatchJournal
from src.dev_agent.providers.fake.provider import FakeProvider
from src.dev_agent.providers.groq import GroqProvider as GroqAdapter
from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
from src.dev_agent.resources.control import ResourceControlPlane
from src.dev_agent.resources.ledger import ResourceLedger
from src.dev_agent.resources.router import ResourceRouter, RouteSelection
from src.dev_agent.resources.survival import SurvivalGovernor
from src.dev_agent.runtime.controller import Controller
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from src.dev_agent.tools.registry import ToolRegistry
from src.dev_agent.tools.runtime import ToolRuntime


def test_provider_runtime_path_roles_are_explicit():
    assert ProviderDispatcher.PROVIDER_PATH_ROLE == "canonical_dispatcher_registry"
    assert Controller.DIRECT_PROVIDER_PATH_ROLE == "compatibility_legacy"


def test_provider_dispatch_journal_persists_prepared_intent(tmp_path):
    ledger, _control = _free_control(tmp_path)
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[{"role": "user", "content": "hello"}])
    selection = RouteSelection("groq-free", "groq", "request", 0, None)

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        journal = ProviderDispatchJournal()
        journal.bind(state_store=store)
        key = journal.prepare_intent(request, selection)

        assert key == f"provider:{request.request_id}:groq-free"
        assert store.get_effect_intent(key)["status"] == "prepared"

    ledger.close()


def _free_control(tmp_path):
    ledger = ResourceLedger(tmp_path / "free-routing.sqlite3")
    for resource_id, provider_id in (("gemini-free", "gemini"), ("groq-free", "groq")):
        ledger.register_resource(
            resource_id,
            provider_id=provider_id,
            native_unit="request",
            capacity=10,
            capabilities=["text"],
            sensitivity="normal",
            cost_minor=0,
            quota_domain=f"domain-{provider_id}",
        )
        ledger.observe(resource_id, available=10, health="healthy")
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger))
    return ledger, control


def test_dispatcher_skips_quota_exhausted_provider_and_uses_secondary(tmp_path):
    ledger, control = _free_control(tmp_path)
    ledger.observe_quota("gemini-free", request_limit=100, request_remaining=0)
    ledger.observe_quota("groq-free", request_limit=100, request_remaining=90)
    calls = []

    class GeminiProvider(FakeProvider):
        provider_id = "gemini"

        def request(self, request):
            calls.append("gemini")
            return ModelResponse(provider="gemini", model="free", text_segments=["must not run"], usage={"cost_minor": 0})

    def groq_backend(request):
        calls.append("groq")
        return {"model": "free", "text_segments": ["secondary"], "usage": {"cost_minor": 0}}

    dispatcher = ProviderDispatcher(ProviderRegistry([GeminiProvider(), GroqAdapter(groq_backend, model="free")]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="quota fallback"))
        audits = store.list_provider_audits()

    assert result.status is TaskStatus.COMPLETED
    assert calls == ["groq"]
    assert [audit["provider_id"] for audit in audits if audit["outcome"] == "succeeded"] == ["groq"]


def test_dispatcher_uses_secondary_after_primary_circuit_opens(tmp_path):
    ledger, control = _free_control(tmp_path)
    ledger.observe_quota("gemini-free", request_limit=100, request_remaining=90)
    ledger.observe_quota("groq-free", request_limit=100, request_remaining=90)
    ledger.record_provider_failure("gemini", threshold=1, cooldown_seconds=60)
    calls = []

    class GeminiProvider(FakeProvider):
        provider_id = "gemini"

        def request(self, request):
            calls.append("gemini")
            raise ProviderError("gemini unavailable", category="transport", retryable=True)

    def groq_backend(request):
        calls.append("groq")
        return {"model": "free", "text_segments": ["secondary"], "usage": {"cost_minor": 0}}

    dispatcher = ProviderDispatcher(ProviderRegistry([GeminiProvider(), GroqAdapter(groq_backend, model="free")]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="outage fallback"))

    assert result.status is TaskStatus.COMPLETED
    assert calls == ["groq"]


def test_dispatcher_ingests_normalized_quota_observation_from_provider_response(tmp_path):
    ledger, control = _free_control(tmp_path)
    ledger.observe_quota("groq-free", request_limit=100, request_remaining=90)

    class QuotaProvider(FakeProvider):
        provider_id = "groq"

        def request(self, request):
            return ModelResponse(
                provider="groq",
                model="free",
                text_segments=["ok"],
                usage={
                    "cost_minor": 0,
                    "quota_observation": {
                        "request_limit": 100,
                        "request_remaining": 88,
                        "source": "provider-response",
                    },
                },
            )

    dispatcher = ProviderDispatcher(ProviderRegistry([QuotaProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="quota telemetry"))

    assert result.status is TaskStatus.COMPLETED
    assert ledger.get_quota_observation("groq-free")["request_remaining"] == 88
    assert ledger.get_quota_observation("groq-free")["source"] == "provider-response"


def test_dispatcher_ignores_malformed_quota_telemetry_without_failing_result(tmp_path):
    ledger, control = _free_control(tmp_path)
    ledger.observe_quota("groq-free", request_limit=100, request_remaining=90)

    class MalformedQuotaProvider(FakeProvider):
        provider_id = "groq"

        def request(self, request):
            return ModelResponse(
                provider="groq",
                model="free",
                text_segments=["ok"],
                usage={"cost_minor": 0, "quota_observation": {"request_remaining": "not-an-integer"}},
            )

    dispatcher = ProviderDispatcher(ProviderRegistry([MalformedQuotaProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="bad quota telemetry"))

    assert result.status is TaskStatus.COMPLETED
    assert ledger.get_quota_observation("groq-free")["request_remaining"] == 90


def test_dispatcher_records_provider_health_through_control_plane(tmp_path, monkeypatch):
    ledger, control = _free_control(tmp_path)
    ledger.observe_quota("groq-free", request_limit=100, request_remaining=90)
    health_calls = []

    monkeypatch.setattr(control, "record_provider_success", lambda provider_id: health_calls.append(("success", provider_id)))
    monkeypatch.setattr(ledger, "record_provider_success", lambda provider_id: (_ for _ in ()).throw(AssertionError("dispatcher crossed into ledger")))

    class HealthyProvider(FakeProvider):
        provider_id = "groq"

        def request(self, request):
            return ModelResponse(provider="groq", model="free", text_segments=["ok"], usage={"cost_minor": 0})

    dispatcher = ProviderDispatcher(ProviderRegistry([HealthyProvider()]), control)
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="health boundary"))

    assert result.status is TaskStatus.COMPLETED
    assert health_calls == [("success", "groq")]


def test_dispatcher_reads_routing_and_budget_snapshots_from_control_plane(tmp_path, monkeypatch):
    ledger, control = _free_control(tmp_path)
    ledger.observe_quota("groq-free", request_limit=100, request_remaining=90)
    calls = []
    routing_snapshot = control.routing_snapshot()
    budget_snapshot = control.budget_snapshot()
    monkeypatch.setattr(control, "routing_snapshot", lambda: calls.append("routing") or routing_snapshot)
    monkeypatch.setattr(control, "budget_snapshot", lambda: calls.append("budget") or budget_snapshot)

    class HealthyProvider(FakeProvider):
        provider_id = "groq"

        def request(self, request):
            return ModelResponse(provider="groq", model="free", text_segments=["ok"], usage={"cost_minor": 0})

    dispatcher = ProviderDispatcher(
        ProviderRegistry([HealthyProvider()]),
        control,
        survival=SurvivalGovernor(),
    )
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        result = Controller(dispatcher, ToolRuntime(ToolRegistry()), store).run(Task(objective="snapshot boundary"))

    assert result.status is TaskStatus.COMPLETED
    assert calls == ["routing", "budget"]
