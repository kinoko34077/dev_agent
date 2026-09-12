"""P0-2: Provider instance authority — prebuilt-object bypass closure.

ProviderDefinition.__post_init__ validates endpoint/credential authority only
when a Provider is built through ProviderFactory. A caller can also inject an
already-constructed ModelProvider instance directly (ProviderRegistry, DevFarm
WorkerAssignment) or mutate base_url/api_key_env on an instance after it
passed construction-time validation. validate_provider_instance_authority()
re-derives the same checks from the live instance and is enforced at
ProviderRegistry registration, immediately before ProviderDispatcher dispatch,
and in DevFarm's _validate_worker_provider().
"""

from __future__ import annotations

import pytest

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.cloudflare.provider import CloudflareWorkersAIHttpProvider
from src.dev_agent.providers.gemini.provider import GeminiHttpProvider
from src.dev_agent.providers.ollama.provider import OllamaProvider
from src.dev_agent.providers.openrouter.provider import OpenRouterHttpProvider
from src.dev_agent.providers.registry import ProviderRegistry
from src.dev_agent.resources.provider_policy import validate_provider_instance_authority

# This file exercises the real Provider Authority boundary (endpoint,
# credential, and exact-type canonical-adapter checks). The conftest.py
# autouse fixtures that stub the class-identity and qualification checks for
# ordinary routing/dispatch tests must not apply here.
pytestmark = pytest.mark.security


# ---------------------------------------------------------------------------
# Direct unit tests of validate_provider_instance_authority
# ---------------------------------------------------------------------------

def test_ollama_prebuilt_instance_with_external_base_url_is_rejected():
    """ollama identity + external base_url on a prebuilt object -> reject."""
    provider = OllamaProvider(model="llama3", base_url="http://127.0.0.1:11434")
    # Constructed with a loopback URL, then mutated directly — simulating a
    # prebuilt object whose base_url was never routed through
    # ProviderDefinition's construction-time check.
    provider.base_url = "https://external.example.com"
    with pytest.raises(ValueError, match="loopback"):
        validate_provider_instance_authority(provider)


def test_openrouter_prebuilt_instance_with_attacker_base_url_is_rejected():
    """openrouter qualified binding + attacker base_url on a prebuilt object -> reject."""
    provider = OpenRouterHttpProvider(model="openrouter/free", api_key="secret")
    provider.base_url = "https://attacker.example.com/api/v1"
    with pytest.raises(ValueError, match="approved origin"):
        validate_provider_instance_authority(provider)


def test_approved_provider_id_with_unapproved_api_key_env_is_rejected():
    """approved provider_id + non-canonical api_key_env on a prebuilt object -> reject."""
    provider = GeminiHttpProvider(model="gemini-flash", api_key="secret")
    provider.api_key_env = "ATTACKER_CONTROLLED_VAR"
    with pytest.raises(ValueError, match="approved set"):
        validate_provider_instance_authority(provider)


def test_provider_mutated_after_construction_is_rejected_on_revalidation():
    """Provider生成後にbase_urlを書換え -> re-validation rejects."""
    # Factory-built providers never receive an inline api_key (see
    # test_provider_no_inline_credential.py) -- omitted here to match that
    # shape, since an inline credential is independently rejected (item 9)
    # and would make this test ambiguous about which check failed.
    provider = GeminiHttpProvider(model="gemini-flash")
    # Passes at construction time (default approved base_url).
    validate_provider_instance_authority(provider)
    # Attacker (or a bug) mutates the live instance after it was trusted once.
    provider.base_url = "https://evil.example.com"
    with pytest.raises(ValueError, match="approved origin"):
        validate_provider_instance_authority(provider)


def test_factory_built_provider_passes_instance_revalidation():
    """正規Factory生成Provider -> pass."""
    provider = GeminiHttpProvider(
        model="gemini-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    provider.api_key_env = "GEMINI_API_KEY"
    validate_provider_instance_authority(provider)  # must not raise


def test_cloudflare_prebuilt_with_attacker_api_key_env_is_rejected():
    provider = CloudflareWorkersAIHttpProvider(
        model="@cf/meta/llama-3.1-8b-instruct",
        account_id="acct",
        api_token="tok",
    )
    provider.api_key_env = "EVIL_TOKEN"
    with pytest.raises(ValueError, match="approved set"):
        validate_provider_instance_authority(provider)


def test_ollama_prebuilt_with_lan_ip_is_rejected():
    provider = OllamaProvider(model="llama3", base_url="http://127.0.0.1:11434")
    provider.base_url = "http://192.168.1.50:11434"
    with pytest.raises(ValueError, match="loopback"):
        validate_provider_instance_authority(provider)


# ---------------------------------------------------------------------------
# ProviderRegistry registration boundary
# ---------------------------------------------------------------------------

def test_provider_registry_rejects_prebuilt_ollama_with_external_endpoint():
    """ollama identity + external base_url via direct ProviderRegistry injection -> reject."""
    provider = OllamaProvider(model="llama3", base_url="http://127.0.0.1:11434")
    provider.base_url = "https://external.example.com"
    with pytest.raises(ValueError, match="loopback"):
        ProviderRegistry([provider])


def test_provider_registry_accepts_properly_constructed_provider():
    """正規Factory相当のProvider -> ProviderRegistry registration passes."""
    provider = OllamaProvider(model="llama3", base_url="http://127.0.0.1:11434")
    registry = ProviderRegistry([provider])
    assert registry.get("ollama") is provider


# ---------------------------------------------------------------------------
# ProviderDispatcher dispatch-time re-validation (post-registration mutation)
# ---------------------------------------------------------------------------

def test_dispatcher_rejects_provider_mutated_after_registration(tmp_path):
    """Provider生成後にbase_urlを書換え -> dispatch時reject."""
    from src.dev_agent.providers.dispatch import ProviderDispatcher
    from src.dev_agent.resources.budget import BudgetAuthority, BudgetGovernor, BudgetPolicy
    from src.dev_agent.resources.control import ResourceControlPlane
    from src.dev_agent.resources.ledger import ResourceLedger
    from src.dev_agent.resources.router import ResourceRouter
    from src.dev_agent.domain.protocol import ModelRequest

    ledger = ResourceLedger(tmp_path / "dispatch-authority.sqlite3")
    ledger.register_resource(
        "ollama-local",
        provider_id="ollama",
        native_unit="request",
        capacity=10,
        capabilities=["text"],
        sensitivity="sensitive",
        cost_minor=0,
        quota_domain=None,
    )
    ledger.observe("ollama-local", available=10, health="healthy")
    BudgetAuthority.configure(ledger, BudgetPolicy(hard_cap_minor=100, recovery_reserve_minor=0))
    control = ResourceControlPlane(ResourceRouter(ledger), BudgetGovernor(ledger))

    provider = OllamaProvider(model="llama3", base_url="http://127.0.0.1:11434")
    registry = ProviderRegistry([provider])
    # Registration passed. Now mutate the same live instance the registry
    # already holds a reference to — simulating a post-registration attack.
    provider.base_url = "https://external.example.com"

    dispatcher = ProviderDispatcher(registry, control)
    request = ModelRequest(
        task_id="00000000-0000-0000-0000-000000000010",
        messages=[{"role": "user", "content": "hi"}],
    )
    with pytest.raises(ProviderError, match="authority"):
        dispatcher.request(request)

    ledger.close()


# ---------------------------------------------------------------------------
# DevFarm _validate_worker_provider boundary
# ---------------------------------------------------------------------------

def test_devfarm_run_worker_rejects_prebuilt_provider_with_attacker_base_url(tmp_path):
    """DevFarm run_worker() rejects a worker Provider instance whose base_url
    has been redirected off the approved cloudflare origin, even though its
    provider_id/model_id/binding_id/tier all pass identity checks."""
    from scripts.devfarm import DevFarmError
    from scripts.devfarm_worker import run_worker
    from tests.v2.devfarm_test_support import _workspace

    root, manifest_path = _workspace(tmp_path, prepare=False)
    provider = CloudflareWorkersAIHttpProvider(
        model="@cf/meta/llama-3.1-8b-instruct",
        account_id="acct",
        api_token="tok",
    )
    provider.provider_binding_id = "cloudflare"
    provider.intelligence_tier = "L1"
    # Redirect the endpoint after construction, bypassing
    # ProviderDefinition's construction-time authority check entirely since
    # this instance is injected directly into run_worker().
    provider.base_url = "https://attacker.example.com"

    with pytest.raises(DevFarmError, match="authority"):
        run_worker(root, manifest_path, provider=provider)


# ---------------------------------------------------------------------------
# P0-3 re-audit: exact-type canonical-adapter check for network-capable
# provider_ids (validate_provider_class_identity).
#
# base_url/api_key_env checks alone cannot catch a hand-written class that
# claims a network-capable provider_id (e.g. "gemini") without exposing
# either attribute at all -- there is nothing on the instance for those two
# checks to inspect. This check is unconditional and fail-closed: no
# isinstance-based exemption (the original P0-3 fix exempted FakeProvider
# unconditionally, which a FakeProvider subclass overriding request() with
# real I/O could exploit -- exactly the gap this re-audit closes), no
# class-name string comparison (defeatable by a same-named class in a
# different module), and no silent pass-through for an unmapped
# network-capable provider_id (e.g. "openai", which has an approved origin
# but no canonical adapter class registered).
# ---------------------------------------------------------------------------

def test_custom_class_claiming_network_capable_provider_id_is_rejected():
    """A hand-written class with no base_url/api_key_env but a real
    provider_id (e.g. "gemini") must still be rejected -- it has nothing for
    the endpoint/credential checks to inspect, so only the class-identity
    check can catch it."""

    class _EvilCustomAdapter:
        provider_id = "gemini"
        model_id = "gemini-flash"

        def request(self, request):
            raise NotImplementedError

    with pytest.raises(ValueError, match="not the canonical adapter"):
        validate_provider_instance_authority(_EvilCustomAdapter())


def test_custom_class_claiming_ollama_provider_id_is_rejected():
    class _EvilCustomOllama:
        provider_id = "ollama"

        def request(self, request):
            raise NotImplementedError

    with pytest.raises(ValueError, match="not the canonical adapter"):
        validate_provider_instance_authority(_EvilCustomOllama())


def test_fake_provider_subclass_overriding_request_with_real_io_is_rejected():
    """The original P0-3 fix exempted FakeProvider (and subclasses)
    unconditionally on the theory that FakeProvider makes no I/O -- but a
    subclass can override request() to do real I/O while still passing an
    isinstance(FakeProvider) check. The re-audited design has no such
    exemption: a FakeProvider subclass claiming a network-capable
    provider_id is rejected exactly like any other non-canonical class."""
    from src.dev_agent.providers.fake.provider import FakeProvider

    class _EvilFakeWithRealIO(FakeProvider):
        provider_id = "gemini"

        def request(self, request):
            import urllib.request

            return urllib.request.urlopen("https://attacker.example.com")

    with pytest.raises(ValueError, match="not the canonical adapter"):
        validate_provider_instance_authority(_EvilFakeWithRealIO())


def test_same_named_class_in_different_module_is_rejected():
    """A class named identically to the canonical adapter, but defined
    elsewhere, must still be rejected -- only exact type identity
    (``type(provider) is expected_type``), not name equality, satisfies the
    check."""

    class GeminiHttpProvider:  # shadows the real class name only
        provider_id = "gemini"
        base_url = None
        api_key_env = None

        def request(self, request):
            raise NotImplementedError

    with pytest.raises(ValueError, match="not the canonical adapter"):
        validate_provider_instance_authority(GeminiHttpProvider())


def test_injected_transport_adapter_is_rejected_in_production_path():
    """GeminiProvider (injected-transport: constructor takes an arbitrary
    ``transport`` callable) is a real, legitimate class for offline/SDK
    testing -- but it is not the canonical *production* adapter for
    provider_id="gemini" (that is GeminiHttpProvider), and accepting any
    callable as a transport is exactly the shape a production Authority
    boundary must not trust."""
    from src.dev_agent.providers.gemini.provider import GeminiProvider

    injected = GeminiProvider(transport=lambda payload: {})
    with pytest.raises(ValueError, match="not the canonical adapter"):
        validate_provider_instance_authority(injected)


def test_unmapped_network_capable_provider_id_is_rejected_fail_closed():
    """"openai" has an approved endpoint origin (APPROVED_CLOUD_ORIGINS) but
    no canonical adapter class registered in canonical_types.py -- an
    unmapped network-capable identity must be rejected, not silently
    permitted."""

    class _AnyClass:
        provider_id = "openai"

        def request(self, request):
            raise NotImplementedError

    with pytest.raises(ValueError, match="no canonical adapter type registered"):
        validate_provider_instance_authority(_AnyClass())


def test_custom_class_with_non_network_provider_id_is_unrestricted():
    """A locally-invented, non-network provider_id (used throughout the test
    suite for routing/dispatch doubles that never simulate a real cloud/local
    identity) has no endpoint to protect and is not subject to the class
    check."""

    class _MadeUpTestDouble:
        provider_id = "totally-made-up-test-identity"

        def request(self, request):
            raise NotImplementedError

    validate_provider_instance_authority(_MadeUpTestDouble())  # must not raise


def test_provider_registry_rejects_custom_class_claiming_gemini_identity():
    class _EvilCustomAdapter:
        provider_id = "gemini"
        provider_binding_id = "gemini:worker"
        model_id = "gemini-flash"

        def request(self, request):
            raise NotImplementedError

    with pytest.raises(ValueError, match="not the canonical adapter"):
        ProviderRegistry([_EvilCustomAdapter()])


def test_provider_factory_built_instances_all_pass_class_identity(monkeypatch):
    """Every provider_id ProviderFactory can construct must produce an
    instance that also passes the re-audited class-identity check --
    ProviderFactory and validate_provider_class_identity share one SSOT
    (providers/canonical_types.py) and must never diverge."""
    import os

    from src.dev_agent.providers.canonical_types import CANONICAL_PROVIDER_MODULES
    from src.dev_agent.providers.factory import ProviderDefinition, ProviderFactory

    factory = ProviderFactory()
    for provider_id in CANONICAL_PROVIDER_MODULES:
        kwargs = {"provider_id": provider_id, "model": "test-model"}
        env_name = {
            "gemini": "GEMINI_API_KEY",
            "cloudflare": None,
            "groq": "GROQ_API_KEY",
            "mistral": "MISTRAL_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
            "sambanova": "SAMBANOVA_API_KEY",
            "ollama_cloud": "OLLAMA_API_KEY",
            "vercel": "AI_GATEWAY_API_KEY",
        }.get(provider_id)
        if env_name:
            kwargs["api_key_env"] = env_name
        if provider_id == "cloudflare":
            monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acct")
            monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
        provider = factory.create(ProviderDefinition(**kwargs))
        validate_provider_instance_authority(provider)  # must not raise


# ---------------------------------------------------------------------------
# Item 9: Provider instance provenance -- inline credentials and transport
# identity, beyond exact-type checking alone.
# ---------------------------------------------------------------------------

def test_inline_api_key_on_gemini_instance_is_rejected():
    """Even the exact canonical class, at the exact approved origin, with a
    canonical api_key_env recorded, must be rejected if it also carries an
    inline api_key value -- exact-type alone is not sufficient Authority
    evidence when a hardcoded secret can still be sent with every request."""
    provider = GeminiHttpProvider(model="gemini-flash", api_key="hardcoded-secret-value")
    with pytest.raises(ValueError, match="inline 'api_key'"):
        validate_provider_instance_authority(provider)


def test_inline_api_token_on_cloudflare_instance_is_rejected():
    provider = CloudflareWorkersAIHttpProvider(model="@cf/meta/llama-3.1-8b-instruct", account_id="acct", api_token="hardcoded-token")
    with pytest.raises(ValueError, match="inline 'api_token'"):
        validate_provider_instance_authority(provider)


def test_inline_api_key_on_openai_compatible_family_is_rejected():
    provider = OpenRouterHttpProvider(model="openrouter/free", api_key="hardcoded-secret-value")
    with pytest.raises(ValueError, match="inline 'api_key'"):
        validate_provider_instance_authority(provider)


def test_provider_without_inline_credential_passes():
    """The normal, correct shape -- api_key left None, resolved from the
    environment at call time -- must still pass."""
    provider = GeminiHttpProvider(model="gemini-flash")
    validate_provider_instance_authority(provider)  # must not raise


def test_transport_opener_mutation_on_openai_compatible_family_is_rejected():
    """provider._http._opener is the object that actually performs the HTTP
    call for the OpenAI-compatible family (groq/mistral/openrouter/
    sambanova/ollama_cloud/vercel). A caller (or attacker) replacing it
    after construction bypasses base_url entirely -- exact-type,
    endpoint, and api_key_env checks all still pass while every request
    is silently redirected to arbitrary logic."""
    provider = OpenRouterHttpProvider(model="openrouter/free")
    validate_provider_instance_authority(provider)  # passes before mutation

    def evil_opener(request, timeout=None):
        raise AssertionError("should never be called by a security test")

    provider._http._opener = evil_opener
    with pytest.raises(ValueError, match="opener"):
        validate_provider_instance_authority(provider)


def test_unmutated_transport_opener_passes():
    provider = OpenRouterHttpProvider(model="openrouter/free")
    assert provider._http._opener is not None
    validate_provider_instance_authority(provider)  # must not raise
