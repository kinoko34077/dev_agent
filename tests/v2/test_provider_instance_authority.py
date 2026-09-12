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
    provider = GeminiHttpProvider(model="gemini-flash", api_key="secret")
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
        api_key="secret",
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
# Concrete adapter-class allowlist for network-capable provider_ids
#
# base_url/api_key_env checks alone cannot catch a hand-written class that
# claims a network-capable provider_id (e.g. "gemini") without exposing
# either attribute at all -- there is nothing on the instance for those two
# checks to inspect. For network-capable provider_ids, the concrete class
# must be an approved adapter. FakeProvider subclasses remain exempt: this
# codebase's established provider-identity test-double convention, and
# FakeProvider is a pure-Python stub with no base_url/api_key_env and no I/O.
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

    with pytest.raises(ValueError, match="not an approved adapter class"):
        validate_provider_instance_authority(_EvilCustomAdapter())


def test_custom_class_claiming_ollama_provider_id_is_rejected():
    class _EvilCustomOllama:
        provider_id = "ollama"

        def request(self, request):
            raise NotImplementedError

    with pytest.raises(ValueError, match="not an approved adapter class"):
        validate_provider_instance_authority(_EvilCustomOllama())


def test_fake_provider_subclass_simulating_network_provider_id_is_exempt():
    """The established test-double convention (FakeProvider subclass with
    provider_id overridden) must remain usable -- it cannot reach a real
    endpoint regardless of which provider_id it claims."""
    from src.dev_agent.providers.fake.provider import FakeProvider

    class _SimulatedGemini(FakeProvider):
        provider_id = "gemini"

    validate_provider_instance_authority(_SimulatedGemini())  # must not raise


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

    with pytest.raises(ValueError, match="not an approved adapter class"):
        ProviderRegistry([_EvilCustomAdapter()])
