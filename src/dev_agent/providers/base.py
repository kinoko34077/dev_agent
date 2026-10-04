"""Provider-neutral interface; SDK objects must not cross this boundary."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from enum import Enum
from http.client import RemoteDisconnected
import hashlib
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
        # Provider decoders may attach a bounded structural projection.  Raw
        # response bodies and exception text must never be stored here.
        self.decode_diagnostics: dict[str, Any] | None = None

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


_DECODE_MAX_KEYS = 32
_DECODE_MAX_NODES = 64
_DECODE_MAX_DEPTH = 3
_DECODE_MAX_LABEL = 64


def _safe_decode_label(value: Any) -> str:
    """Return a bounded structural label without retaining arbitrary text."""

    if isinstance(value, str):
        label = value.strip()
    else:
        label = type(value).__name__
    if not label:
        return "<empty>"
    label = label[:_DECODE_MAX_LABEL]
    return label if re.fullmatch(r"[A-Za-z0-9_.:-]+", label) else "<redacted>"


def _decode_value_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, bytes):
        return "bytes"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "array"
    return "other"


def _bounded_decode_size(value: Any, *, depth: int = 0) -> int:
    """Estimate response size without serializing or retaining response data."""

    if depth > _DECODE_MAX_DEPTH:
        return 0
    if isinstance(value, (bytes, bytearray)):
        return min(len(value), 1_000_000)
    if isinstance(value, str):
        return min(len(value.encode("utf-8", errors="replace")), 1_000_000)
    if isinstance(value, Mapping):
        total = 2
        for index, (key, item) in enumerate(value.items()):
            if index >= _DECODE_MAX_KEYS:
                total += 64
                break
            total += min(len(str(key).encode("utf-8", errors="replace")), _DECODE_MAX_LABEL)
            total += _bounded_decode_size(item, depth=depth + 1)
        return min(total, 1_000_000)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        total = 2
        for index, item in enumerate(value):
            if index >= _DECODE_MAX_KEYS:
                total += 64
                break
            total += _bounded_decode_size(item, depth=depth + 1)
        return min(total, 1_000_000)
    return len(type(value).__name__)


def _decode_size_bucket(size: int) -> str:
    if size <= 0:
        return "empty"
    if size <= 256:
        return "tiny"
    if size <= 4_096:
        return "small"
    if size <= 16_384:
        return "medium"
    if size <= 65_536:
        return "large"
    return "oversized"


def project_decode_structure(
    raw: Any,
    *,
    decoder_branch: str,
    http_status: int | None = None,
    content_type: str | None = None,
) -> dict[str, Any]:
    """Project bounded response shape facts without exposing response data."""

    nodes: list[dict[str, str]] = []
    truncated = False

    def visit(value: Any, path: str, depth: int) -> None:
        nonlocal truncated
        if len(nodes) >= _DECODE_MAX_NODES:
            truncated = True
            return
        nodes.append({"path": path, "kind": _decode_value_kind(value)})
        if depth >= _DECODE_MAX_DEPTH:
            return
        if isinstance(value, Mapping):
            for index, (key, item) in enumerate(value.items()):
                if index >= _DECODE_MAX_KEYS:
                    truncated = True
                    break
                safe_key = _safe_decode_label(key)
                visit(item, f"{path}.{safe_key}" if path else safe_key, depth + 1)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for index, item in enumerate(value[:_DECODE_MAX_KEYS]):
                visit(item, f"{path}[{index}]", depth + 1)
            if len(value) > _DECODE_MAX_KEYS:
                truncated = True

    visit(raw, "", 0)
    top_level_keys: list[str] = []
    if isinstance(raw, Mapping):
        for index, key in enumerate(raw.keys()):
            if index >= _DECODE_MAX_KEYS:
                truncated = True
                break
            top_level_keys.append(_safe_decode_label(key))
    top_level_keys = sorted(set(top_level_keys))
    shape = "|".join(f"{item['path']}={item['kind']}" for item in nodes)
    normalized_content_type = None
    if isinstance(content_type, str) and content_type.strip():
        normalized_content_type = content_type.split(";", 1)[0].strip().lower()[:64]
    safe_status = http_status if isinstance(http_status, int) and not isinstance(http_status, bool) else None
    return {
        "decoder_branch": _safe_decode_label(decoder_branch),
        "http_status": safe_status,
        "content_type": normalized_content_type,
        "top_level_keys": top_level_keys,
        "value_kinds": nodes,
        "nested_paths": [item["path"] for item in nodes if item["path"]],
        "size_bucket": _decode_size_bucket(_bounded_decode_size(raw)),
        "schema_fingerprint": hashlib.sha256(shape.encode("utf-8")).hexdigest()[:16],
        "truncated": truncated,
    }


def attach_decode_diagnostics(
    error: ProviderError,
    raw: Any,
    *,
    decoder_branch: str,
    http_status: int | None = None,
    content_type: str | None = None,
) -> ProviderError:
    """Attach only a bounded structural projection to a decode failure."""

    error.decode_diagnostics = project_decode_structure(
        raw,
        decoder_branch=decoder_branch,
        http_status=http_status,
        content_type=content_type,
    )
    return error


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
    if isinstance(cause, (ConnectionResetError, ConnectionAbortedError, RemoteDisconnected)):
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
    # Provider adapters initialize the transport stage to RESPONSE_WAIT before
    # opening a socket.  That is only a last-known stage, not proof that an
    # arbitrary OSError reached response waiting.  Preserve the useful default
    # for timeout failures, but do not label opaque socket-policy failures
    # (for example Windows WSAEACCES/10013) more precisely than observed.
    cause = _transport_cause(error)
    if explicit is TransportStage.RESPONSE_WAIT and cause is not None and cause is not error and not isinstance(cause, TimeoutError):
        return TransportStage.UNKNOWN
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

    def probe_liveness(self) -> None:
        """Confirm bounded provider liveness without generating model output.

        Adapters may implement this with a provider-owned read-only endpoint.
        The default is deliberately unsupported so an injected or legacy
        adapter cannot be treated as healthy merely because it is registered.
        """

        raise ProviderError(
            f"{self.provider_id} does not expose a bounded liveness probe",
            category="unsupported_capability",
            retryable=False,
        )
