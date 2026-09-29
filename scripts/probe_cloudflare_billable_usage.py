#!/usr/bin/env python3
"""Probe Cloudflare's read-only Alpha/Restricted account billable usage API.

The probe uses existing CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN values. It
never changes a token, role, billing plan, or subscription. A 403 is reported as
an access boundary because the billing API requires Billing Read and the usage
endpoint is Alpha/Restricted.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json

from src.dev_agent.providers.base import ProviderError
from src.dev_agent.providers.cloudflare.billing_introspection import CloudflareBillableUsageClient


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).date().isoformat(),
        help="UTC usage date (YYYY-MM-DD); defaults to the current UTC date",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    client = CloudflareBillableUsageClient()
    try:
        state = client.read_day(args.date)
    except ProviderError as exc:
        if exc.category == "authentication":
            print(
                json.dumps(
                    {
                        "status": "AUTH_REQUIRED",
                        "provider": "cloudflare",
                        "network_called": False,
                        "credential_env": ["CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN"],
                        "next_step": "Provide the already-authorized Cloudflare account/token values; do not create or widen permissions from this command.",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 2
        if exc.category == "authorization":
            print(
                json.dumps(
                    {
                        "status": "ACCESS_REQUIRED_OR_RESTRICTED",
                        "provider": "cloudflare",
                        "network_called": True,
                        "required_permission": "Account > Billing > Read",
                        "endpoint": "/accounts/{account_id}/billable/usage",
                        "endpoint_status": "Alpha/Restricted",
                        "next_step": "Human decides whether to supply Billing Read / endpoint access. No permission mutation is performed here.",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 3
        raise

    print(json.dumps({"status": "OBSERVED_RAW", **state}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
