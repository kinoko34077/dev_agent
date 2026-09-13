"""Provider-neutral interface; SDK objects must not cross this boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..domain.protocol import ModelRequest, ModelResponse


class ProviderError(RuntimeError):
    """A provider failed before returning a normalized response."""

    _FAILOVER_SAFE_CATEGORIES = frozenset(
        {
            "authentication",
            "authorization",
            "rate_limit",
            "quota",
            "provider_unavailable",
            "provider_execution_saturated",
        }
    )

    def __init__(
        self,
        message: str,
        *,
        category: str | None = None,
        retryable: bool | None = None,
        failover_safe: bool | None = None,
        http_status: int | None = None,
        quota_metric: str | None = None,
        quota_window: str | None = None,
        quota_reset_at: str | None = None,
        quota_reset_source: str | None = None,
    ) -> None:
        super().__init__(message)
        prefix = message.split(":", 1)[0].strip()
        self.category = category or (prefix if prefix in {"transport", "authentication", "authorization", "rate_limit", "quota", "provider_unavailable", "provider_execution_saturated", "provider_http", "provider_decode", "provider_context", "context_limit", "output_limit", "safety_block", "unsupported_capability"} else "provider_http")
        self.retryable = retryable if retryable is not None else self.category in {"transport", "rate_limit", "quota"}
        # ``retryable`` answers whether the same binding may be called again.
        # ``failover_safe`` answers whether a different eligible binding may
        # be selected.  Transport/decode failures remain conservative because
        # the external outcome may be unknown, while confirmed auth/quota/
        # availability failures can safely move to another binding.
        self.failover_safe = failover_safe if failover_safe is not None else self.category in self._FAILOVER_SAFE_CATEGORIES
        self.http_status = http_status
        self.quota_metric = quota_metric
        self.quota_window = quota_window
        self.quota_reset_at = quota_reset_at
        self.quota_reset_source = quota_reset_source

    @property
    def requires_reconciliation(self) -> bool:
        """Whether the provider boundary may have produced an external effect.

        Transport/decode failures and unknown HTTP failures happen after an
        external request may have crossed the provider boundary.  A normal
        client-side rejection (for example HTTP 400/401/403/429) is treated as
        a confirmed no-charge outcome, while server errors and status-less
        adapter failures remain ambiguous.
        """
        if self.category in {"transport", "provider_decode", "reconciliation_required"}:
            return True
        return self.category == "provider_http" and (self.http_status is None or self.http_status == 408 or self.http_status >= 500)


class ModelProvider(ABC):
    provider_id: str = "unknown"

    @abstractmethod
    def request(self, request: ModelRequest) -> ModelResponse:
        """Return a normalized response for a normalized request."""
