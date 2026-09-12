"""P1-1: HTTP redirect authority — no Provider adapter may follow a redirect.

provider_policy.validate_endpoint_authority() validates base_url once, but
urllib's default HTTPRedirectHandler follows a server's 3xx response
transparently — including to a different origin — and forwards the
Authorization header regardless of host. urlopen_no_redirect() (built on a
_NoRedirectHandler that refuses every redirect) closes that gap for all
Provider HTTP adapters.

These tests run a real local HTTP server (loopback only) so the redirect
behavior under test is urllib's actual wire-level handling, not a mock.
"""

from __future__ import annotations

import http.server
import threading

import pytest

from src.dev_agent.domain.protocol import ModelRequest
from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.cloudflare.provider import CloudflareWorkersAIHttpProvider
from src.dev_agent.providers.gemini.provider import GeminiHttpProvider
from src.dev_agent.providers.groq.provider import GroqHttpProvider
from src.dev_agent.providers.ollama.provider import OllamaProvider
from src.dev_agent.providers.openai_compatible.http import OpenAICompatibleHttpTransport


def _request(task_id="00000000-0000-0000-0000-0000000000aa"):
    return ModelRequest(task_id=task_id, messages=[{"role": "user", "content": "hi"}], max_output_tokens=8)


class _RedirectServer:
    """A loopback-only HTTP server that answers every POST with a redirect."""

    def __init__(self, *, status: int, location: str, capture_auth: bool = False):
        self.status = status
        self.location = location
        self.capture_auth = capture_auth
        self.captured_auth_headers: list[str | None] = []
        self.hit_count = 0
        captured_auth_headers = self.captured_auth_headers
        hit_counter = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(handler_self):
                hit_counter.hit_count += 1
                if capture_auth:
                    captured_auth_headers.append(handler_self.headers.get("Authorization"))
                handler_self.send_response(status)
                handler_self.send_header("Location", location)
                handler_self.end_headers()

            def log_message(handler_self, *_args):
                pass

        self._server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._server.shutdown()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


# ---------------------------------------------------------------------------
# 1. approved endpoint -> attacker origin redirect -> reject
# ---------------------------------------------------------------------------

def test_approved_endpoint_redirect_to_attacker_origin_is_rejected():
    with _RedirectServer(status=302, location="https://attacker.example.com/steal") as server:
        provider = GroqHttpProvider(model="m", api_key="secret", base_url=server.base_url, timeout_seconds=2)
        with pytest.raises(ProviderError) as exc_info:
            provider.request(_request())
        assert "redirect" in str(exc_info.value).lower()
        assert exc_info.value.retryable is False


# ---------------------------------------------------------------------------
# 2. approved endpoint -> same-origin path redirect -> rejected by policy
#    (fail-closed default: no redirect is followed regardless of origin)
# ---------------------------------------------------------------------------

def test_same_origin_redirect_is_also_rejected_by_default_policy():
    with _RedirectServer(status=307, location="/v2/chat/completions") as server:
        provider = GroqHttpProvider(model="m", api_key="secret", base_url=server.base_url, timeout_seconds=2)
        with pytest.raises(ProviderError) as exc_info:
            provider.request(_request())
        assert "redirect" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 3. Ollama loopback -> external redirect -> reject
# ---------------------------------------------------------------------------

def test_ollama_loopback_redirect_to_external_host_is_rejected():
    with _RedirectServer(status=302, location="https://external.example.com/api/chat") as server:
        provider = OllamaProvider(model="llama3", base_url=server.base_url, timeout_seconds=2)
        with pytest.raises(ProviderError) as exc_info:
            provider.request(_request())
        assert "redirect" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# 4. credential header is never forwarded to a redirect target
# ---------------------------------------------------------------------------

def test_credential_header_is_never_forwarded_across_redirect():
    with _RedirectServer(
        status=302,
        location="https://attacker.example.com/collect",
        capture_auth=True,
    ) as server:
        transport = OpenAICompatibleHttpTransport()
        with pytest.raises(ProviderError):
            transport.post_json(
                provider_id="test",
                url=f"{server.base_url}/v1/chat/completions",
                payload={"x": 1},
                api_key="SUPER-SECRET-KEY",
                timeout_seconds=2,
            )
        # Exactly one request reached the server (the original), and it
        # carried the credential because that request IS the approved
        # endpoint. No second request was made anywhere — the redirect
        # target never saw a request at all, so it never saw the header.
        assert server.hit_count == 1
        assert server.captured_auth_headers == ["Bearer SUPER-SECRET-KEY"]


# ---------------------------------------------------------------------------
# Positive guard: a normal (non-redirecting) approved endpoint still works
# ---------------------------------------------------------------------------

def test_normal_response_without_redirect_is_not_affected():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            import json as _json

            body = _json.dumps(
                {
                    "model": "m",
                    "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"total_tokens": 1},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = GroqHttpProvider(model="m", api_key="secret", base_url=f"http://127.0.0.1:{port}", timeout_seconds=2)
        response = provider.request(_request())
        assert response.text_segments == ["ok"]
    finally:
        server.shutdown()


def test_cloudflare_redirect_is_rejected():
    with _RedirectServer(status=301, location="https://attacker.example.com/") as server:
        provider = CloudflareWorkersAIHttpProvider(
            model="@cf/meta/llama-3.1-8b-instruct",
            account_id="acct",
            api_token="tok",
            base_url=server.base_url,
            timeout_seconds=2,
        )
        with pytest.raises(ProviderError) as exc_info:
            provider.request(_request())
        assert "redirect" in str(exc_info.value).lower()


def test_gemini_redirect_is_rejected():
    with _RedirectServer(status=303, location="https://attacker.example.com/") as server:
        provider = GeminiHttpProvider(model="gemini-flash", api_key="secret", base_url=server.base_url, timeout_seconds=2)
        with pytest.raises(ProviderError) as exc_info:
            provider.request(_request())
        assert "redirect" in str(exc_info.value).lower()
