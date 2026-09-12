"""P0: Provider Binding Authority — endpoint and credential binding tests.

An attacker must not be able to obtain local-provider treatment (no
qualification, max_sensitivity=sensitive, privacy_profile=local_only) by
setting provider_id="ollama" with a non-loopback base_url, nor redirect cloud
provider credential resolution to an attacker-controlled variable.

7 required negative tests + positive guard (approved endpoint + credential).
"""

from __future__ import annotations

import pytest

from src.dev_agent.providers.factory import ProviderDefinition


# ---------------------------------------------------------------------------
# Negative tests (7)
# ---------------------------------------------------------------------------

def test_ollama_with_external_https_is_rejected():
    """ollama + external https → reject (local treatment with remote routing)."""
    with pytest.raises(ValueError, match="loopback"):
        ProviderDefinition(
            provider_id="ollama",
            model="llama3",
            base_url="https://external.example.com",
        )


def test_ollama_with_lan_private_ip_is_rejected():
    """ollama + LAN/private IP → reject."""
    for lan_url in [
        "http://192.168.1.100:11434",
        "http://10.0.0.5:11434",
        "http://172.16.0.1:11434",
    ]:
        with pytest.raises(ValueError, match="loopback"):
            ProviderDefinition(provider_id="ollama", model="llama3", base_url=lan_url)


def test_ollama_with_non_loopback_localhost_alias_with_public_name_is_rejected():
    """ollama + hostname that is not a loopback alias → reject."""
    with pytest.raises(ValueError, match="loopback"):
        ProviderDefinition(
            provider_id="ollama",
            model="llama3",
            base_url="http://my-ollama-host.internal:11434",
        )


def test_qualified_openrouter_with_attacker_base_url_is_rejected():
    """Qualified OpenRouter + attacker base_url → reject."""
    with pytest.raises(ValueError, match="approved origin"):
        ProviderDefinition(
            provider_id="openrouter",
            model="openrouter/free",
            base_url="https://attacker.example.com/api/v1",
        )


def test_qualified_gemini_with_attacker_base_url_is_rejected():
    """Qualified Gemini + attacker base_url → reject."""
    with pytest.raises(ValueError, match="approved origin"):
        ProviderDefinition(
            provider_id="gemini",
            model="gemini-flash",
            base_url="https://attacker.example.com/v1beta",
        )


def test_qualified_binding_with_different_api_key_env_is_rejected():
    """Qualified binding + non-canonical api_key_env → reject."""
    with pytest.raises(ValueError, match="approved set"):
        ProviderDefinition(
            provider_id="gemini",
            model="gemini-flash",
            api_key_env="ATTACKER_API_KEY",
        )


def test_cloudflare_with_attacker_api_key_env_is_rejected():
    """Cloudflare binding + non-canonical api_key_env → reject."""
    with pytest.raises(ValueError, match="approved set"):
        ProviderDefinition(
            provider_id="cloudflare",
            model="@cf/meta/llama-3.1-8b-instruct",
            api_key_env="EVIL_TOKEN",
        )


def test_openrouter_with_attacker_api_key_env_is_rejected():
    """OpenRouter binding + non-canonical api_key_env → reject."""
    with pytest.raises(ValueError, match="approved set"):
        ProviderDefinition(
            provider_id="openrouter",
            model="openrouter/free",
            api_key_env="MY_CUSTOM_KEY",
        )


# ---------------------------------------------------------------------------
# Positive tests — approved endpoint + credential must pass
# ---------------------------------------------------------------------------

def test_approved_ollama_loopback_endpoint_passes():
    """ollama + loopback → accepted."""
    for loopback_url in [
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://[::1]:11434",
    ]:
        d = ProviderDefinition(provider_id="ollama", model="llama3", base_url=loopback_url)
        assert d.base_url == loopback_url.rstrip("/")


def test_approved_gemini_origin_and_api_key_env_passes():
    """Gemini + approved base_url + approved api_key_env → accepted."""
    d = ProviderDefinition(
        provider_id="gemini",
        model="gemini-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key_env="GEMINI_API_KEY",
    )
    assert d.provider_id == "gemini"


def test_approved_openrouter_origin_passes():
    """OpenRouter + approved base_url → accepted."""
    d = ProviderDefinition(
        provider_id="openrouter",
        model="openrouter/free",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
    )
    assert d.provider_id == "openrouter"


def test_approved_cloudflare_origin_and_api_key_env_passes():
    """Cloudflare + approved base_url + approved api_key_env → accepted."""
    d = ProviderDefinition(
        provider_id="cloudflare",
        model="@cf/meta/llama-3.1-8b-instruct",
        base_url="https://api.cloudflare.com/client/v4",
        api_key_env="CLOUDFLARE_API_TOKEN",
    )
    assert d.provider_id == "cloudflare"


def test_no_base_url_override_always_passes():
    """No base_url override → no endpoint restriction applies."""
    d = ProviderDefinition(provider_id="gemini", model="gemini-flash")
    assert d.base_url is None


def test_fake_provider_with_any_base_url_passes():
    """fake (test stub) → no endpoint restriction."""
    d = ProviderDefinition(provider_id="fake", model="fake-model", base_url="http://anything.example.com")
    assert d.provider_id == "fake"
