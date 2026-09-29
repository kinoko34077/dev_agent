#!/usr/bin/env python3
"""Capture raw non-secret rate-limit headers from one bounded inference request.

This helper is intentionally observational. It never creates credentials,
changes billing, alters Router/Gate state, or derives strict eligibility.
Without an existing provider key it exits as AUTH_REQUIRED before network.
"""

from __future__ import annotations

import argparse
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request

from src.dev_agent.providers.openai_compatible.http import urlopen_no_redirect


PROVIDERS = {
    "groq": {
        "env": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "token_field": "max_completion_tokens",
    },
    "mistral": {
        "env": "MISTRAL_API_KEY",
        "url": "https://api.mistral.ai/v1/chat/completions",
        "token_field": "max_tokens",
    },
}


def _rate_headers(headers) -> dict[str, str]:
    result: dict[str, str] = {}
    if headers is None or not hasattr(headers, "items"):
        return result
    for key, value in headers.items():
        lower = str(key).lower()
        if lower == "retry-after" or "ratelimit" in lower:
            result[lower] = str(value).strip()
    return dict(sorted(result.items()))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=sorted(PROVIDERS), required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()

    config = PROVIDERS[args.provider]
    key = os.environ.get(config["env"])
    if not key:
        print(
            json.dumps(
                {
                    "status": "AUTH_REQUIRED",
                    "provider": args.provider,
                    "network_called": False,
                    "credential_env": config["env"],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": "Reply with OK."}],
        config["token_field"]: 2,
        "stream": False,
    }
    request = Request(
        config["url"],
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen_no_redirect(request, timeout=20.0) as response:
            response.read(1024 * 1024)
            status = "OBSERVED"
            http_status = getattr(response, "status", 200)
            headers = _rate_headers(response.headers)
    except HTTPError as exc:
        status = "ACCESS_REQUIRED" if exc.code in {401, 403} else "RATE_LIMITED" if exc.code == 429 else "HTTP_ERROR"
        http_status = exc.code
        headers = _rate_headers(exc.headers)
    except (URLError, OSError) as exc:
        print(json.dumps({"status": "TRANSPORT_ERROR", "provider": args.provider, "network_called": True, "error": type(exc).__name__}, indent=2, sort_keys=True))
        return 3

    print(
        json.dumps(
            {
                "status": status,
                "provider": args.provider,
                "model": args.model,
                "network_called": True,
                "http_status": http_status,
                "rate_limit_headers": headers,
                "admission_ready": False,
                "interpretation": "Raw provider rate-limit headers only; no strict eligibility is derived by this probe.",
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if status == "OBSERVED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
