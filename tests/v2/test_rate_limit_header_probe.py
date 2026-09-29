import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "probe_rate_limit_headers.py"
_spec = importlib.util.spec_from_file_location("probe_rate_limit_headers", SCRIPT)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


def test_rate_headers_keeps_only_rate_limit_metadata():
    headers = {
        "Authorization": "secret",
        "X-RateLimit-Limit-Requests": "1000",
        "X-RateLimit-Remaining-Requests": "999",
        "Retry-After": "2",
        "Content-Type": "application/json",
    }

    observed = _module._rate_headers(headers)

    assert observed == {
        "retry-after": "2",
        "x-ratelimit-limit-requests": "1000",
        "x-ratelimit-remaining-requests": "999",
    }
    assert "authorization" not in observed


def test_provider_configs_use_existing_credential_envs_and_bounded_endpoints():
    assert _module.PROVIDERS["groq"]["env"] == "GROQ_API_KEY"
    assert _module.PROVIDERS["groq"]["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert _module.PROVIDERS["mistral"]["env"] == "MISTRAL_API_KEY"
    assert _module.PROVIDERS["mistral"]["url"] == "https://api.mistral.ai/v1/chat/completions"
