"""Read-only Cloudflare billable-usage introspection for Track C.

Cloudflare's account billable-usage API is an Alpha/Restricted billing surface.
It can provide raw daily metered quantities, including free-tier consumption,
but this module deliberately does not derive Workers AI remaining neurons or a
ResourceRouter quota observation.  Exact Workers AI metric identity, current-
day freshness, endpoint entitlement, and quota-domain mapping must be observed
and accepted separately.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
import os
from typing import Any
from urllib.parse import urlencode

from ..base import ProviderError
from ..openai_compatible.http import OpenAICompatibleHttpTransport


class CloudflareBillableUsageError(ValueError):
    """Cloudflare returned billing usage metadata that cannot be trusted."""


def _mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CloudflareBillableUsageError(f"{name} must be an object")
    return value


def _day(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("day must be an ISO 8601 date")
    normalized = value.strip()
    try:
        return date.fromisoformat(normalized).isoformat()
    except ValueError as exc:
        raise ValueError("day must be an ISO 8601 date") from exc


class CloudflareBillableUsageClient:
    """Inspect raw account metered usage with existing Cloudflare authority."""

    def __init__(
        self,
        *,
        account_id: str | None = None,
        api_token: str | None = None,
        base_url: str = "https://api.cloudflare.com/client/v4",
        timeout_seconds: float = 15.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url must be a non-empty string")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.account_id = account_id
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._http = OpenAICompatibleHttpTransport(opener)

    def _credentials(self) -> tuple[str, str]:
        account_id = self.account_id or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        api_token = self.api_token or os.environ.get("CLOUDFLARE_API_TOKEN")
        if not isinstance(account_id, str) or not account_id.strip() or not isinstance(api_token, str) or not api_token.strip():
            raise ProviderError(
                "cloudflare authentication failed: CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required",
                category="authentication",
                retryable=False,
            )
        return account_id.strip(), api_token.strip()

    @staticmethod
    def _normalize_record(raw: Any) -> dict[str, Any]:
        record = _mapping(raw, name="Cloudflare billable usage record")
        quantity = record.get("ConsumedQuantity")
        unit = record.get("ConsumedUnit")
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            raise CloudflareBillableUsageError("ConsumedQuantity must be numeric")
        if not isinstance(unit, str) or not unit.strip():
            raise CloudflareBillableUsageError("ConsumedUnit must be a non-empty string")
        allowed = (
            "ChargePeriodStart",
            "ChargePeriodEnd",
            "ConsumedQuantity",
            "ConsumedUnit",
            "x_BillableMetricId",
            "x_BillableMetricName",
            "x_ProductCategoryName",
            "x_ProductFamilyId",
            "x_ProductFamilyName",
            "BilledCost",
        )
        return {
            key: record[key]
            for key in allowed
            if key in record and isinstance(record[key], (str, int, float)) and not isinstance(record[key], bool)
        }

    def read_day(self, day: str) -> dict[str, Any]:
        normalized_day = _day(day)
        account_id, api_token = self._credentials()  # Fail before network when credentials are absent.
        query = urlencode({"from": normalized_day, "to": normalized_day})
        raw, _headers = self._http.get_json(
            provider_id="cloudflare-billing",
            url=f"{self.base_url}/accounts/{account_id}/billable/usage?{query}",
            api_key=api_token,
            timeout_seconds=self.timeout_seconds,
        )
        document = _mapping(raw, name="Cloudflare billable usage response")
        if document.get("success") is not True:
            raise CloudflareBillableUsageError("Cloudflare billable usage response was not successful")
        rows = document.get("result")
        if not isinstance(rows, list):
            raise CloudflareBillableUsageError("Cloudflare billable usage result must be an array")
        records = [self._normalize_record(row) for row in rows]
        neuron_records = [
            record
            for record in records
            if str(record.get("ConsumedUnit", "")).strip().lower() in {"neuron", "neurons"}
        ]
        return {
            "schema_version": 1,
            "evidence_type": "cloudflare_daily_billable_usage_raw",
            "authority": "cloudflare-billable-usage-alpha",
            "restricted_endpoint": True,
            "day": normalized_day,
            "records": records,
            "neuron_records": neuron_records,
            "admission_ready": False,
            "interpretation": (
                "Raw first-party daily metered usage only. Do not derive strict Workers AI quota headroom "
                "until an actual Workers AI neuron metric is observed and its freshness, free-allocation reset, "
                "endpoint entitlement, and quota-domain semantics are accepted."
            ),
        }


__all__ = ["CloudflareBillableUsageClient", "CloudflareBillableUsageError"]
