from io import BytesIO
from urllib.error import HTTPError

from src.dev_agent.providers.openrouter.introspection import OpenRouterIntrospectionClient


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n=-1):
        return self.payload if n < 0 else self.payload[:n]


def test_read_state_survives_management_only_credits_endpoint_for_regular_key():
    calls: list[str] = []

    def fake_open(request, timeout):
        calls.append(request.full_url)
        if request.full_url.endswith("/key"):
            return _Response(
                b'{"data":{"label":"regular-key","limit":0,"limit_remaining":0,"is_free_tier":true}}'
            )
        if request.full_url.endswith("/credits"):
            raise HTTPError(
                request.full_url,
                403,
                "Forbidden",
                {},
                BytesIO(b'{"error":{"message":"Management key required"}}'),
            )
        if request.full_url.endswith("/models"):
            return _Response(
                b'{"data":[{"id":"example/free:free","pricing":{"prompt":"0","completion":"0"},"supported_parameters":["tools"]}]}'
            )
        raise AssertionError(request.full_url)

    state = OpenRouterIntrospectionClient(
        api_key="regular-key",
        opener=fake_open,
        timeout_seconds=3,
    ).read_state(required_parameters={"tools"})

    assert state["spend_control"]["is_free_tier"] is True
    assert state["credits"] is None
    assert state["credits_status"] == "MANAGEMENT_KEY_REQUIRED"
    assert [item["id"] for item in state["zero_price_candidates"]] == ["example/free:free"]
    assert calls == [
        "https://openrouter.ai/api/v1/key",
        "https://openrouter.ai/api/v1/credits",
        "https://openrouter.ai/api/v1/models",
    ]
