"""Provider-neutral interface; SDK objects must not cross this boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from http.client import RemoteDisconnected
import re
import socket
import ssl
from typing import Any
from urllib.error import URLError

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
    DNS_FAILURE = "dns_failure"
    CONNECT_FAILURE = "connect_failure"
    CONNECTION_RESET = "connection_reset"
    TLS_FAILURE = "tls_failure"
    CONNECT_TIMEOUT = "connect_timeout"
    READ_TIMEOUT = "read_timeout"
    PROVIDER_TRANSPORT_FAILURE = "provider_transport_failure"
    TRANSPORT_UNCLASSIFIED = "transport_unclassified"


class TransportStage(str, Enum):
    """Last bounded stage known to have been reached by an HTTP transport."""

    UNKNOWN = "unknown"
    RESOLVE = "resolve"
    CONNECT = "connect"
    TLS = "tls"
    REQUEST_SEND = "request_send"
    RESPONSE_WAIT = "response_wait"
    RESPONSE_READ = "response_read"


_SAFE_TRANSPORT_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SAFE_TRANSPORT_INT_MIN = -(2**31)
_SAFE_TRANSPORT_INT_MAX = 2**31 - 1


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


def _transport_cause(error: BaseException) -> BaseException | None:
    """Return one bounded non-wrapper cause for diagnostic projection."""

    fallback: BaseException | None = None
    for cause in _iter_transport_causes(error):
        if isinstance(cause, ProviderError):
            continue
        if isinstance(cause, URLError):
            fallback = fallback or cause
            continue
        return cause
    return fallback


def _coerce_transport_stage(value: object) -> TransportStage | None:
    if isinstance(value, TransportStage):
        return value
    if isinstance(value, str):
        try:
            return TransportStage(value)
        except ValueError:
            return None
    return None


def _safe_transport_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not (_SAFE_TRANSPORT_INT_MIN <= value <= _SAFE_TRANSPORT_INT_MAX):
        return None
    return value


def _safe_transport_type(value: object) -> str | None:
    if isinstance(value, str) and _SAFE_TRANSPORT_TYPE.fullmatch(value):
        return value
    return None


def _cause_stage(cause: BaseException | None) -> TransportStage | None:
    if cause is None:
        return None
    if isinstance(cause, (socket.gaierror, socket.herror)):
        return TransportStage.RESOLVE
    if isinstance(cause, ssl.SSLError):
        return TransportStage.TLS
    if isinstance(cause, ConnectionRefusedError):
        return TransportStage.CONNECT
    if isinstance(cause, RemoteDisconnected):
        return TransportStage.RESPONSE_WAIT
    if isinstance(cause, BrokenPipeError):
        return TransportStage.REQUEST_SEND
    return None


def _cause_matches_errno(cause: BaseException | None, values: frozenset[int]) -> bool:
    if cause is None:
        return False
    return any(
        _safe_transport_int(getattr(cause, attribute, None)) in values
        for attribute in ("errno", "winerror")
    )


def _transport_stage_for(error: BaseException, stage: TransportStage | None) -> TransportStage:
    inferred = _cause_stage(_transport_cause(error))
    if inferred is not None:
        return inferred
    explicit = stage or _coerce_transport_stage(getattr(error, "transport_stage", None))
    return explicit or TransportStage.UNKNOWN


def classify_transport_failure(
    error: BaseException,
    *,
    execution_boundary: str | None = None,
    stage: TransportStage | str | None = None,
) -> TransportFailureCategory:
    """Classify the failing layer using explicit Host execution evidence.

    A Windows error number alone cannot identify whether Codex, the local
    process policy, or the provider transport blocked the request.  The Host
    composition must therefore provide the execution boundary explicitly.
    This projection never changes ProviderError retry, failover, or
    reconciliation control flow.
    """

    preserved = getattr(error, "transport_failure_category", None)
    if isinstance(preserved, str):
        try:
            return TransportFailureCategory(preserved)
        except ValueError:
            pass

    if execution_boundary == "codex_sandbox" and _has_local_socket_denial(error):
        return TransportFailureCategory.SANDBOX_NETWORK_DENIED
    if execution_boundary == "host_process" and _has_local_socket_denial(error):
        return TransportFailureCategory.LOCAL_NETWORK_POLICY_DENIED

    cause = _transport_cause(error)
    if isinstance(cause, (socket.gaierror, socket.herror)):
        return TransportFailureCategory.DNS_FAILURE
    if isinstance(cause, ssl.SSLError):
        return TransportFailureCategory.TLS_FAILURE
    if isinstance(cause, ConnectionRefusedError) or _cause_matches_errno(cause, frozenset({61, 111, 10061})):
        return TransportFailureCategory.CONNECT_FAILURE
    if isinstance(cause, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, RemoteDisconnected)) or _cause_matches_errno(cause, frozenset({32, 103, 104, 10053, 10054})):
        return TransportFailureCategory.CONNECTION_RESET
    if isinstance(cause, TimeoutError):
        resolved_stage = _transport_stage_for(error, _coerce_transport_stage(stage))
        if resolved_stage in {TransportStage.CONNECT, TransportStage.RESOLVE, TransportStage.TLS, TransportStage.REQUEST_SEND}:
            return TransportFailureCategory.CONNECT_TIMEOUT
        if resolved_stage in {TransportStage.RESPONSE_WAIT, TransportStage.RESPONSE_READ}:
            return TransportFailureCategory.READ_TIMEOUT
    if execution_boundary == "provider_process" and isinstance(error, ProviderError) and error.category == "transport":
        return TransportFailureCategory.PROVIDER_TRANSPORT_FAILURE
    return TransportFailureCategory.TRANSPORT_UNCLASSIFIED


def project_transport_failure(
    error: BaseException,
    *,
    execution_boundary: str | None = None,
    stage: TransportStage | str | None = None,
) -> dict[str, str | int | None]:
    """Project transport diagnostics without exposing exception text.

    The projection is deliberately observational.  It never changes
    ``ProviderError.retryable``, ``failover_safe``, or reconciliation state.
    Exception type, errno, winerror, category, and last-known stage are all
    finite scalar values suitable for a bounded Host artifact.
    """

    stage_value = _coerce_transport_stage(stage)
    category = classify_transport_failure(error, execution_boundary=execution_boundary, stage=stage_value)
    cause = _transport_cause(error)
    explicit_type = _safe_transport_type(getattr(error, "transport_exception_type", None))
    explicit_errno = _safe_transport_int(getattr(error, "transport_errno", None))
    explicit_winerror = _safe_transport_int(getattr(error, "transport_winerror", None))
    inferred_stage = _transport_stage_for(error, stage_value)
    return {
        "transport_failure_category": category.value,
        "transport_stage": inferred_stage.value,
        "transport_exception_type": explicit_type or (_safe_transport_type(type(cause).__name__) if cause is not None else None),
        "transport_errno": explicit_errno if explicit_errno is not None else _safe_transport_int(getattr(cause, "errno", None)),
        "transport_winerror": explicit_winerror if explicit_winerror is not None else _safe_transport_int(getattr(cause, "winerror", None)),
    }


def annotate_transport_failure(
    error: ProviderError,
    *,
    execution_boundary: str | None = None,
    stage: TransportStage | str | None = None,
    cause: BaseException | None = None,
) -> ProviderError:
    """Attach the bounded transport projection to a typed ProviderError."""

    if not isinstance(error, ProviderError):
        raise TypeError("error must be a ProviderError")
    if cause is not None:
        if not isinstance(cause, BaseException):
            raise TypeError("cause must be a BaseException or None")
        error.__cause__ = cause
    projection = project_transport_failure(
        error,
        execution_boundary=execution_boundary,
        stage=stage,
    )
    for key, value in projection.items():
        setattr(error, key, value)
    return error


def transport_failure_metadata(error: BaseException) -> dict[str, str | int]:
    """Return only present, validated transport fields for an outer artifact."""

    if getattr(error, "category", None) != "transport" and not any(
        hasattr(error, field)
        for field in (
            "transport_failure_category",
            "transport_stage",
            "transport_exception_type",
            "transport_errno",
            "transport_winerror",
        )
    ):
        return {}
    projection = project_transport_failure(error)
    metadata: dict[str, str | int] = {}
    for key, value in projection.items():
        if value is not None:
            metadata[key] = value
    return metadata


class ModelProvider(ABC):
    provider_id: str = "unknown"

    @abstractmethod
    def request(self, request: ModelRequest) -> ModelResponse:
        """Return a normalized response for a normalized request."""
