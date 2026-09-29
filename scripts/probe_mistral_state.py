#!/usr/bin/env python3
"""Read-only Mistral model discovery for Track C.

This command never performs inference. Without an existing MISTRAL_API_KEY it
returns AUTH_REQUIRED before network access.
"""

from __future__ import annotations

import json

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.mistral.introspection import MistralIntrospectionClient


def main() -> int:
    client = MistralIntrospectionClient()
    try:
        state = client.read_state()
    except ProviderError as exc:
        if exc.category == "authentication":
            print(
                json.dumps(
                    {
                        "status": "AUTH_REQUIRED",
                        "provider": "mistral",
                        "network_called": False,
                        "credential_env": "MISTRAL_API_KEY",
                        "next_step": (
                            "Provide an existing Mistral API key, then rerun this read-only model discovery. "
                            "Do not create a key or enable billing from this command."
                        ),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 2
        raise

    print(json.dumps({"status": "OBSERVED_READ_ONLY", **state}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
