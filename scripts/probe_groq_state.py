#!/usr/bin/env python3
"""Read-only Groq model discovery for Track C.

This command never performs inference.  Without an existing GROQ_API_KEY it
returns AUTH_REQUIRED before network access.  Rate-limit headroom remains a
separate bounded inference-evidence step because Groq publishes those values on
inference response headers, not the Models API.
"""

from __future__ import annotations

import json

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.groq.introspection import GroqIntrospectionClient


def main() -> int:
    client = GroqIntrospectionClient()
    try:
        state = client.read_state()
    except ProviderError as exc:
        if exc.category == "authentication":
            print(
                json.dumps(
                    {
                        "status": "AUTH_REQUIRED",
                        "provider": "groq",
                        "network_called": False,
                        "credential_env": "GROQ_API_KEY",
                        "next_step": (
                            "Provide an existing Groq API key, then rerun this read-only model discovery. "
                            "Do not create or enable billing from this command."
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
