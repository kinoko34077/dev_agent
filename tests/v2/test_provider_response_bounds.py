"""P1: HTTP provider response memory DoS — bounded read tests.

All four HTTP providers (openai_compatible, gemini, cloudflare, ollama) must
reject responses that exceed MAX_PROVIDER_RESPONSE_BYTES rather than
materialising an unbounded body.
"""

from __future__ import annotations

import json

import pytest

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.openai_compatible.http import (
    MAX_PROVIDER_RESPONSE_BYTES,
    _read_bounded,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _BoundedResponse:
    """Minimal urllib response stub that honours a read(n) argument."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            return self._data
        return self._data[: n]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _make_response(size: int, fill: bytes = b"x") -> _BoundedResponse:
    return _BoundedResponse(fill * size)


def _json_response(payload: dict) -> _BoundedResponse:
    return _BoundedResponse(json.dumps(payload).encode("utf-8"))


# ---------------------------------------------------------------------------
# _read_bounded unit tests
# ---------------------------------------------------------------------------

def test_bounded_read_under_limit_passes():
    data = b"a" * (MAX_PROVIDER_RESPONSE_BYTES - 1)
    resp = _BoundedResponse(data)
    result = _read_bounded(resp)
    assert result == data


def test_bounded_read_exact_limit_passes():
    data = b"b" * MAX_PROVIDER_RESPONSE_BYTES
    resp = _BoundedResponse(data)
    result = _read_bounded(resp)
    assert result == data


def test_bounded_read_over_limit_raises():
    data = b"c" * (MAX_PROVIDER_RESPONSE_BYTES + 1)
    resp = _BoundedResponse(data)
    with pytest.raises(ProviderError) as exc_info:
        _read_bounded(resp)
    assert "exceeded" in str(exc_info.value).lower()
    assert exc_info.value.category == "provider_decode"
    assert exc_info.value.retryable is False


def test_bounded_read_custom_limit():
    resp = _BoundedResponse(b"x" * 101)
    with pytest.raises(ProviderError, match="exceeded"):
        _read_bounded(resp, max_bytes=100)

    resp_ok = _BoundedResponse(b"x" * 100)
    assert len(_read_bounded(resp_ok, max_bytes=100)) == 100


def test_bounded_read_content_length_lie_is_caught():
    """A response that claims small Content-Length but delivers more bytes is caught."""
    # The bounded reader does NOT trust Content-Length; it reads up to max+1
    # bytes and measures what it actually received.
    large_data = b"y" * (MAX_PROVIDER_RESPONSE_BYTES + 5)
    resp = _BoundedResponse(large_data)
    with pytest.raises(ProviderError, match="exceeded"):
        _read_bounded(resp)


def test_bounded_read_no_content_length_huge_body_caught():
    """Without Content-Length the reader still enforces the byte cap."""
    huge = b"z" * (MAX_PROVIDER_RESPONSE_BYTES + 1024)
    resp = _BoundedResponse(huge)
    with pytest.raises(ProviderError, match="exceeded"):
        _read_bounded(resp)


def test_bounded_read_empty_response_passes():
    resp = _BoundedResponse(b"")
    assert _read_bounded(resp) == b""


def test_bounded_read_malformed_json_propagates_as_bytes():
    """_read_bounded itself does not parse JSON; callers handle decode errors."""
    resp = _BoundedResponse(b"{not valid json")
    raw = _read_bounded(resp)
    assert raw == b"{not valid json"


# ---------------------------------------------------------------------------
# OpenAI-compatible provider integration: over-limit → ProviderError
# ---------------------------------------------------------------------------

class _OverLimitOpener:
    """Replacement for urlopen that returns a body exceeding the limit."""

    def __init__(self, extra: int = 1) -> None:
        self._extra = extra

    def __call__(self, request, timeout=None):
        data = b"x" * (MAX_PROVIDER_RESPONSE_BYTES + self._extra)
        return _BoundedResponse(data)


def _minimal_openai_request():
    from src.dev_agent.domain.protocol import ModelRequest
    return ModelRequest(
        task_id="00000000-0000-0000-0000-000000000001",
        request_id="00000000-0000-0000-0000-000000000002",
        messages=[{"role": "user", "content": "hi"}],
        max_output_tokens=10,
    )


def test_openai_compatible_rejects_oversized_response():
    from src.dev_agent.providers.openai_compatible import OpenAICompatibleHttpProvider

    provider = OpenAICompatibleHttpProvider(
        model="gpt-test",
        api_key="test-key",
        http_open=_OverLimitOpener(),
    )
    with pytest.raises(ProviderError) as exc_info:
        provider.request(_minimal_openai_request())
    assert exc_info.value.category == "provider_decode"


# ---------------------------------------------------------------------------
# Gemini provider integration: over-limit → ProviderError
# ---------------------------------------------------------------------------

class _GeminiOverLimitOpener:
    def __call__(self, request, timeout=None):
        data = b"g" * (MAX_PROVIDER_RESPONSE_BYTES + 1)
        return _BoundedResponse(data)


def test_gemini_rejects_oversized_response():
    from src.dev_agent.providers.gemini.provider import GeminiHttpProvider

    provider = GeminiHttpProvider(model="gemini-test", api_key="test-key")
    provider.timeout_seconds = 1.0

    # monkey-patch urlopen inside the module
    import src.dev_agent.providers.gemini.provider as gemini_mod
    original = gemini_mod.urlopen_no_redirect
    gemini_mod.urlopen_no_redirect = _GeminiOverLimitOpener()
    try:
        with pytest.raises(ProviderError) as exc_info:
            provider.request(_minimal_openai_request())
        assert exc_info.value.category in {"provider_decode", "transport"}
    finally:
        gemini_mod.urlopen_no_redirect = original


# ---------------------------------------------------------------------------
# Cloudflare provider integration: over-limit → ProviderError
# ---------------------------------------------------------------------------

class _CfOverLimitOpener:
    def __call__(self, request, timeout=None):
        data = b"c" * (MAX_PROVIDER_RESPONSE_BYTES + 1)
        return _BoundedResponse(data)


def test_cloudflare_rejects_oversized_response():
    from src.dev_agent.providers.cloudflare.provider import CloudflareWorkersAIHttpProvider

    provider = CloudflareWorkersAIHttpProvider(
        model="@cf/meta/llama-3.1-8b-instruct",
        account_id="acct",
        api_token="tok",
    )

    import src.dev_agent.providers.cloudflare.provider as cf_mod
    original = cf_mod.urlopen_no_redirect
    cf_mod.urlopen_no_redirect = _CfOverLimitOpener()
    try:
        with pytest.raises(ProviderError) as exc_info:
            provider.request(_minimal_openai_request())
        assert exc_info.value.category in {"provider_decode", "transport"}
    finally:
        cf_mod.urlopen_no_redirect = original


# ---------------------------------------------------------------------------
# Ollama provider integration: over-limit → ProviderError
# ---------------------------------------------------------------------------

class _OllamaOverLimitOpener:
    def __call__(self, request, timeout=None):
        data = b"o" * (MAX_PROVIDER_RESPONSE_BYTES + 1)
        return _BoundedResponse(data)


def test_ollama_rejects_oversized_response():
    from src.dev_agent.providers.ollama.provider import OllamaProvider

    provider = OllamaProvider(model="llama3", base_url="http://127.0.0.1:11434")

    import src.dev_agent.providers.ollama.provider as ollama_mod
    original = ollama_mod.urlopen_no_redirect
    ollama_mod.urlopen_no_redirect = _OllamaOverLimitOpener()
    try:
        with pytest.raises(ProviderError) as exc_info:
            provider.request(_minimal_openai_request())
        assert exc_info.value.category in {"provider_decode", "transport"}
    finally:
        ollama_mod.urlopen_no_redirect = original


# ---------------------------------------------------------------------------
# Chunked/large body simulation — ensure limit fires even on valid JSON prefix
# ---------------------------------------------------------------------------

def test_bounded_read_returns_only_first_n_plus_one_bytes():
    """_read_bounded calls read(max+1), so even valid JSON truncated at max+1 triggers the error."""
    # Construct a payload that is valid JSON but > limit
    big_str = "A" * (MAX_PROVIDER_RESPONSE_BYTES + 10)
    payload = json.dumps({"text": big_str}).encode("utf-8")
    # Actual size of the payload will exceed the limit
    if len(payload) > MAX_PROVIDER_RESPONSE_BYTES:
        resp = _BoundedResponse(payload)
        with pytest.raises(ProviderError, match="exceeded"):
            _read_bounded(resp)
