"""Provider-neutral interface; SDK objects must not cross this boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

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


class TransportFailureCategory(str, Enum):
    """Bounded diagnostic categories for an outbound transport failure."""

    SANDBOX_NETWORK_DENIED = "sandbox_network_denied"
    LOCAL_NETWORK_POLICY_DENIED = "local_network_policy_denied"
    PROVIDER_TRANSPORT_FAILURE = "provider_transport_failure"
    TRANSPORT_UNCLASSIFIED = "transport_unclassified"


def _iter_transport_causes(error: BaseException):
    """Yield a bounded exception cause/reason chain without logging text."""

    pending: list[Any] = [error]
    seen: set[int] = set()
    while pending and len(seen) < 8:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for attribute in ("__cause__", "__context__", "reason"):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException) and id(nested) not in seen:
                pending.append(nested)


def _has_local_socket_denial(error: BaseException) -> bool:
    return any(
        getattr(cause, "winerror", None) == 10013 or getattr(cause, "errno", None) == 10013
        for cause in _iter_transport_causes(error)
    )


def classify_transport_failure(
    error: BaseException,
    *,
    execution_boundary: str | None = None,
) -> TransportFailureCategory:
    """Classify the failing layer using explicit Host execution evidence.

    A Windows error number alone cannot identify whether Codex, the local
    process policy, or the provider transport blocked the request.  The Host
    composition must therefore provide the execution boundary explicitly.
    This projection never changes ProviderError retry, failover, or
    reconciliation control flow.
    """

    if execution_boundary == "codex_sandbox" and _has_local_socket_denial(error):
        return TransportFailureCategory.SANDBOX_NETWORK_DENIED
    if execution_boundary == "host_process" and _has_local_socket_denial(error):
        return TransportFailureCategory.LOCAL_NETWORK_POLICY_DENIED
    if execution_boundary == "provider_process" and isinstance(error, ProviderError) and error.category == "transport":
        return TransportFailureCategory.PROVIDER_TRANSPORT_FAILURE
    return TransportFailureCategory.TRANSPORT_UNCLASSIFIED


class ModelProvider(ABC):
    provider_id: str = "unknown"

    @abstractmethod
    def request(self, request: ModelRequest) -> ModelResponse:
        """Return a normalized response for a normalized request."""
