import json

from src.dev_agent.providers.cloudflare.provider import CloudflareWorkersAIHttpProvider
from src.dev_agent.providers.model_discovery import ModelDiscoveryBinding, ProviderModelDiscovery


MODEL_ID = "@cf/zai-org/glm-4.7-flash"


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, n=-1):
        return self.payload if n < 0 else self.payload[:n]


def test_cloudflare_discovery_uses_openrouter_dispatch_identity_surface():
    captured = []
    secrets = {
        "CLOUDFLARE_ACCOUNT_ID": "account-123",
        "CLOUDFLARE_API_TOKEN": "existing-token",
    }

    def fake_get(url, headers, timeout):
        captured.append((url, dict(headers), timeout))
        return {"data": [{"id": MODEL_ID}]}

    result = ProviderModelDiscovery(secret_getter=secrets.get).discover(
        ModelDiscoveryBinding(
            provider_id="cloudflare",
            provider_binding_id="cloudflare:account",
            api_key_env="CLOUDFLARE_API_TOKEN",
            account_id_env="CLOUDFLARE_ACCOUNT_ID",
        ),
        http_get=fake_get,
    )

    assert [entry.model_id for entry in result.entries] == [MODEL_ID]
    assert captured[0][0] == (
        "https://api.cloudflare.com/client/v4/accounts/account-123/ai/models/search"
        "?format=openrouter&per_page=1000&hide_experimental=false&include_deprecated=false"
    )
    assert captured[0][1]["Authorization"] == "Bearer existing-token"


def test_cloudflare_liveness_checks_the_same_dispatch_identity_surface(monkeypatch):
    captured = []

    def fake_open(request, timeout):
        captured.append((request.full_url, request.method, timeout))
        return _Response({"data": [{"id": MODEL_ID}]})

    monkeypatch.setattr(
        "src.dev_agent.providers.cloudflare.provider.urlopen_no_redirect",
        fake_open,
    )

    CloudflareWorkersAIHttpProvider(
        model=MODEL_ID,
        account_id="account-123",
        api_token="existing-token",
        timeout_seconds=4,
    ).probe_liveness()

    assert captured == [
        (
            "https://api.cloudflare.com/client/v4/accounts/account-123/ai/models/search"
            "?format=openrouter&per_page=1000&hide_experimental=false&include_deprecated=false",
            "GET",
            4.0,
        )
    ]
