#!/usr/bin/env python3
"""Read-only Mistral Admin observation for Track C.

Requires an already-existing MISTRAL_ADMIN_API_KEY. Missing auth stops before
network. The command does not create credentials, alter limits/billing, perform
inference, or promote observations into runtime eligibility.
"""

from __future__ import annotations

import json

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.mistral.admin import MistralAdminObservationClient, MistralAdminObservationError


def main() -> int:
    client = MistralAdminObservationClient()
    try:
        state = client.read_state()
    except ProviderError as exc:
        network_called = exc.http_status is not None or exc.category in {"transport", "rate_limit", "provider_http", "authorization"}
        status = "AUTH_REQUIRED" if exc.category == "authentication" and not network_called else "ACCESS_REQUIRED" if exc.category in {"authentication", "authorization"} else "OBSERVATION_FAILED"
        print(
            json.dumps(
                {
                    "status": status,
                    "provider": "mistral",
                    "surface": "admin",
                    "network_called": network_called,
                    "http_status": exc.http_status,
                    "credential_env": "MISTRAL_ADMIN_API_KEY",
                    "next_step": (
                        "Provide an existing Mistral Admin API key with read access and rerun. "
                        "Do not create/rotate a key or change billing/limits from this command."
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2
    except MistralAdminObservationError as exc:
        print(
            json.dumps(
                {
                    "status": "OBSERVATION_INVALID",
                    "provider": "mistral",
                    "surface": "admin",
                    "network_called": True,
                    "error": str(exc),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 3

    print(json.dumps({"status": "OBSERVED_READ_ONLY", **state}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
