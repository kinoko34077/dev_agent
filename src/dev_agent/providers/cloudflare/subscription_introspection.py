"""Read-only Cloudflare account subscription *hints*, never billing authority.

Cloudflare GET /accounts/{account_id}/subscriptions is an account Billing Read
endpoint (not the Alpha/Restricted billable-usage endpoint). The bounded,
redacted projection below is solely for human confirmation of account mode.
It does not attest Workers AI plan/free overage safety, prepaid AI Gateway
credits, quota headroom, or exact model/credential eligibility. No production
Resource/BillingResolver/Router imports this observation.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from .billing_introspection import CloudflareBillableUsageClient


class CloudflareSubscriptionsError(ValueError):
    """A subscription listing is malformed or too large for safe review."""


_MAX_SUBSCRIPTIONS = 64
_KNOWN_STATES = frozenset({
    "Trial", "Provisioned", "Paid", "AwaitingPayment",
    "Cancelled", "Failed", "Expired",
})
# These are *UI label hints*, not reviewed plan identities or a billing grant.
_LABEL_HINTS = {
    "workers free": "WORKERS_FREE_LABEL_ONLY",
    "workers paid": "WORKERS_PAID_LABEL_ONLY",
}


class CloudflareSubscriptionsClient(CloudflareBillableUsageClient):
    """Query one existing Cloudflare account with existing Billing Read access."""

    @staticmethod
    def _subscription_hint(raw: Any) -> dict[str, str]:
        if not isinstance(raw, Mapping):
            raise CloudflareSubscriptionsError("subscription row must be an object")
        plan = raw.get("rate_plan")
        if plan is not None and not isinstance(plan, Mapping):
            raise CloudflareSubscriptionsError("subscription rate_plan must be an object")
        plan_name = plan.get("public_name") if isinstance(plan, Mapping) else None
        hint = (
            _LABEL_HINTS.get(plan_name.strip().casefold(), "UNRECOGNIZED")
            if isinstance(plan_name, str) and len(plan_name) <= 100
            else "UNRECOGNIZED"
        )
        state = raw.get("state")
        # Never copy arbitrary account-owned name, subscription ID, price,
        # currency, invoicing details, service sets or upstream payload.
        return {
            "plan_label_hint": hint,
            "state_hint": state if isinstance(state, str) and state in _KNOWN_STATES else "UNKNOWN",
        }

    def read_subscriptions(self) -> dict[str, Any]:
        account_id, api_token = self._credentials()  # No network without auth.
        raw, _headers = self._http.get_json(
            provider_id="cloudflare-subscriptions",
            url=f"{self.base_url}/accounts/{account_id}/subscriptions",
            api_key=api_token,
            timeout_seconds=self.timeout_seconds,
        )
        if not isinstance(raw, Mapping) or raw.get("success") is not True:
            raise CloudflareSubscriptionsError("subscription response must be a successful object")
        entries = raw.get("result")
        if not isinstance(entries, list) or len(entries) > _MAX_SUBSCRIPTIONS:
            raise CloudflareSubscriptionsError("subscription result must be a bounded list")
        result_info = raw.get("result_info")
        total = result_info.get("total_count") if isinstance(result_info, Mapping) else None
        listing_complete = (
            isinstance(total, int) and not isinstance(total, bool)
            and total == len(entries)
        )
        return {
            "schema_version": 1,
            "evidence_type": "cloudflare_account_subscriptions_review_only",
            "authority": "cloudflare_account_billing_read",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "subscription_count_returned": len(entries),
            "listing_complete_by_count": listing_complete,
            "plan_hints": [self._subscription_hint(entry) for entry in entries],
            "admission_ready": False,
            "trusted_no_charge": False,
            "interpretation": (
                "Subscription label hints only. An empty/partial listing does not prove "
                "Workers Free. Even an apparent Workers Free/Paid label must be matched "
                "by a human to the actual Workers AI account, current paid-overage and "
                "AI Gateway mode; no quota or runtime admission is implied."
            ),
        }


__all__ = ["CloudflareSubscriptionsClient", "CloudflareSubscriptionsError"]
