#!/usr/bin/env python3
"""Read only Cloudflare account subscription labels with existing Billing Read.

No credit purchase, plan mutation, token exchange, source timestamp renewal,
cost guarantee, quota inference, generation, or runtime/Phase 8 promotion.
The result intentionally omits account IDs, tokens, subscription IDs, raw
plan names, prices, invoices, and all Cloudflare response objects.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.cloudflare.subscription_introspection import (
    CloudflareSubscriptionsClient,
    CloudflareSubscriptionsError,
)


def main() -> int:
    try:
        observation = CloudflareSubscriptionsClient().read_subscriptions()
    except ProviderError as exc:
        if exc.category == "authentication":
            # Local credential absence and a remote HTTP 401 are different
            # observations; never claim no network call after a 401.
            remote_rejection = exc.http_status is not None
            print(json.dumps({
                "status": "AUTH_REJECTED" if remote_rejection else "AUTH_REQUIRED",
                "network_called": remote_rejection,
                "existing_env": ["CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"],
                "next_step": (
                    "Existing supplied credentials were rejected; human must check read authority, without rotating automatically."
                    if remote_rejection else
                    "Existing authorized account ID and Billing Read token required; do not widen privileges."
                ),
            }, sort_keys=True))
            return 3 if remote_rejection else 2
        if exc.category == "authorization":
            print(json.dumps({
                "status": "ACCESS_REQUIRED", "network_called": True,
                "permission": "Account > Billing > Read",
                "endpoint": "GET /accounts/{account_id}/subscriptions",
                "next_step": "Human must confirm existing Billing Read access; do not create, rotate, or elevate a token.",
            }, sort_keys=True))
            return 3
        print(json.dumps({"status": "READ_FAILED", "category": "provider_or_transport", "admission_ready": False}))
        return 1
    except (CloudflareSubscriptionsError, ValueError, TypeError):
        print(json.dumps({"status": "INVALID_RESPONSE", "admission_ready": False}))
        return 1
    print(json.dumps({"status": "OBSERVED_UNVERIFIED", **observation}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
