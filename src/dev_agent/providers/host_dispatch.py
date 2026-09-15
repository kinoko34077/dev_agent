"""Host-owned, one-shot outbound provider invocation boundary.

This module deliberately does not select resources, retry, reconcile, or own
approval.  Those responsibilities remain with ``ProviderDispatcher`` and the
existing Host authorities.  The boundary exists so callers such as the
development Worker do not invoke a concrete provider inline as an implicit
policy decision; an Agent/Host runtime can inject the same bounded invocation
without exposing credentials or endpoints to the caller.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import re
from pathlib import Path
import json
import subprocess
from uuid import uuid4

from ..domain.protocol import ModelRequest, ModelResponse
from ..resources.provider_policy import validate_provider_instance_authority
from .base import ModelProvider, ProviderError, TransportFailureCategory, classify_transport_failure


_EXECUTION_BOUNDARIES = frozenset(
    {"unclassified", "codex_sandbox", "host_process", "provider_process"}
)
_DIGEST = re.compile(r"^[0-9a-fA-F]{64}$")
_SAFE_DIAGNOSTIC_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")
_ENVELOPE_FIELDS = frozenset(
    {
        "dispatch_id",
        "provider_id",
        "provider_binding_id",
        "model_id",
        "intelligence_tier",
        "request",
        "egress_manifest_sha256",
    }
)


@dataclass(frozen=True)
class HostDispatchEnvelope:
    """Non-secret one-shot intent accepted by a Host provider runtime."""

    dispatch_id: str
    provider_id: str
    provider_binding_id: str
    model_id: str
    intelligence_tier: str
    request: ModelRequest
    egress_manifest_sha256: str

    def __post_init__(self) -> None:
        for name in ("dispatch_id", "provider_id", "provider_binding_id", "model_id", "intelligence_tier"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            object.__setattr__(self, name, value.strip())
        if self.intelligence_tier not in {"L0", "L1", "L2", "L3"}:
            raise ValueError("intelligence_tier is invalid")
        if not isinstance(self.request, ModelRequest):
            raise TypeError("request must be a ModelRequest")
        if not isinstance(self.egress_manifest_sha256, str) or _DIGEST.fullmatch(self.egress_manifest_sha256) is None:
            raise ValueError("egress_manifest_sha256 must be a SHA-256 digest")
        object.__setattr__(self, "egress_manifest_sha256", self.egress_manifest_sha256.lower())

    def to_dict(self) -> dict[str, object]:
        return {
            "dispatch_id": self.dispatch_id,
            "provider_id": self.provider_id,
            "provider_binding_id": self.provider_binding_id,
            "model_id": self.model_id,
            "intelligence_tier": self.intelligence_tier,
            "request": self.request.to_dict(),
            "egress_manifest_sha256": self.egress_manifest_sha256,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "HostDispatchEnvelope":
        if not isinstance(value, dict):
            raise ValueError("Host dispatch envelope must be an object")
        unknown = set(value) - _ENVELOPE_FIELDS
        if unknown:
            raise ValueError(f"unknown Host dispatch field: {sorted(unknown)[0]}")
        request = value.get("request")
        if not isinstance(request, dict):
            raise ValueError("Host dispatch request must be an object")
        try:
            return cls(
                dispatch_id=value.get("dispatch_id"),
                provider_id=value.get("provider_id"),
                provider_binding_id=value.get("provider_binding_id"),
                model_id=value.get("model_id"),
                intelligence_tier=value.get("intelligence_tier"),
                request=ModelRequest.from_dict(request),
                egress_manifest_sha256=value.get("egress_manifest_sha256"),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid Host dispatch envelope: {exc}") from exc


class HostProviderDispatch:
    """Invoke one already Host-admitted provider exactly once.

    The provider may be a concrete adapter or the canonical
    ``ProviderDispatcher``.  This class intentionally has no alternate-route
    or retry behavior.  A failure is returned to the existing caller so that
    ``ProviderDispatcher``/reconciliation semantics are not duplicated here.
    """

    def __init__(
        self,
        provider: ModelProvider,
        *,
        execution_boundary: str = "unclassified",
        executor: Callable[[ModelProvider, ModelRequest], ModelResponse] | None = None,
    ) -> None:
        if not hasattr(provider, "request") or not callable(provider.request):
            raise TypeError("provider must expose a callable request method")
        if execution_boundary not in _EXECUTION_BOUNDARIES:
            raise ValueError(f"execution_boundary must be one of {sorted(_EXECUTION_BOUNDARIES)}")
        if executor is not None and not callable(executor):
            raise TypeError("executor must be callable or None")
        try:
            validate_provider_instance_authority(provider)
        except ValueError:
            # DevFarm's existing caller performs the same Host admission for
            # concrete providers.  Keep this boundary structural for test
            # doubles and already-composed ProviderDispatcher instances, while
            # never weakening the concrete worker validation path.
            provider_id = getattr(provider, "provider_id", None)
            if provider_id not in {"fake", "resource-router"}:
                raise
        self._provider = provider
        self._executor = executor
        self.execution_boundary = execution_boundary
        self.last_transport_category: TransportFailureCategory | None = None

    @property
    def provider_identity(self) -> dict[str, str | None]:
        """Return bounded non-secret identity labels only."""

        tier = getattr(getattr(self._provider, "intelligence_tier", None), "value", None)
        if tier is None:
            tier = getattr(self._provider, "intelligence_tier", None)
        return {
            "provider_id": getattr(self._provider, "provider_id", None),
            "provider_binding_id": getattr(self._provider, "provider_binding_id", None),
            "model_id": getattr(self._provider, "model_id", None) or getattr(self._provider, "model", None),
            "intelligence_tier": tier,
        }

    def request(self, request: ModelRequest) -> ModelResponse:
        """Perform exactly one provider invocation and preserve typed failures."""

        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")
        self.last_transport_category = None
        try:
            response = (
                self._executor(self._provider, request)
                if self._executor is not None
                else self._provider.request(request)
            )
        except ProviderError as exc:
            preserved = getattr(exc, "transport_failure_category", None)
            # The diagnostic field describes outbound transport only.  A
            # Host admission/configuration rejection or a confirmed provider
            # response must remain available through ``exc.category`` rather
            # than being mislabeled as an unexplained transport failure.
            if preserved is None and exc.category not in {"transport"}:
                self.last_transport_category = None
                raise
            try:
                self.last_transport_category = (
                    TransportFailureCategory(preserved)
                    if isinstance(preserved, str)
                    else classify_transport_failure(
                        exc,
                        execution_boundary=None if self.execution_boundary == "unclassified" else self.execution_boundary,
                    )
                )
            except ValueError:
                self.last_transport_category = classify_transport_failure(
                    exc,
                    execution_boundary=None if self.execution_boundary == "unclassified" else self.execution_boundary,
                )
            raise
        if not isinstance(response, ModelResponse):
            raise TypeError("provider must return ModelResponse")
        return response

    def envelope(self, request: ModelRequest, *, egress_manifest_sha256: str, dispatch_id: str | None = None) -> HostDispatchEnvelope:
        """Create a Host intent without copying endpoint or credential settings."""

        if not isinstance(request, ModelRequest):
            raise TypeError("request must be a ModelRequest")
        identity = self.provider_identity
        intelligence_tier = identity["intelligence_tier"]
        if not isinstance(intelligence_tier, str) or not intelligence_tier.strip():
            # A routed temporary provider may not carry the tier on its
            # concrete adapter.  Reuse the Host-selected exact tier already
            # bound into the request; never infer it from a model name.
            allowed_tiers = request.metadata.get("allowed_intelligence_tiers")
            if (
                isinstance(allowed_tiers, (list, tuple))
                and len(allowed_tiers) == 1
                and isinstance(allowed_tiers[0], str)
                and allowed_tiers[0] in {"L0", "L1", "L2", "L3"}
            ):
                intelligence_tier = allowed_tiers[0]
        if not isinstance(intelligence_tier, str) or not intelligence_tier.strip():
            raise ValueError("provider intelligence_tier must be available from Host admission")
        return HostDispatchEnvelope(
            dispatch_id=dispatch_id or uuid4().hex,
            provider_id=identity["provider_id"],
            provider_binding_id=identity["provider_binding_id"],
            model_id=identity["model_id"],
            intelligence_tier=intelligence_tier,
            request=request,
            egress_manifest_sha256=egress_manifest_sha256,
        )


class HostRoutedDispatcher:
    """Keep canonical routing in-process while moving the concrete call out.

    ``ProviderDispatcher`` remains responsible for selection, reservation,
    durable intent, failover, and reconciliation.  This adapter only supplies
    its binding-scoped execution callback, so Planner/Reviewer/Critic callers
    can use the same Host process boundary as Workers without introducing a
    second router or retry loop.
    """

    provider_id = "resource-router"
    handles_resource_policy = True

    def __init__(
        self,
        dispatcher: object,
        executor: Callable[[ModelProvider, ModelRequest], ModelResponse],
    ) -> None:
        if not callable(getattr(dispatcher, "request_with_execution", None)):
            raise TypeError("dispatcher must expose request_with_execution")
        if not callable(executor):
            raise TypeError("executor must be callable")
        self._dispatcher = dispatcher
        self._executor = executor

    @property
    def audits(self):
        """Expose the canonical dispatch audit view without copying policy."""

        return getattr(self._dispatcher, "audits", ())

    def request(
        self,
        request_or_task_id: ModelRequest | str,
        explicit_request: ModelRequest | None = None,
    ) -> ModelResponse:
        """Forward one request through Dispatcher-owned selection and accounting."""

        if explicit_request is None:
            if not isinstance(request_or_task_id, ModelRequest):
                raise TypeError("request must be a ModelRequest")
            request = request_or_task_id
        else:
            if not isinstance(request_or_task_id, str):
                raise TypeError("explicit task_id must be a string")
            if request_or_task_id != explicit_request.task_id:
                raise ValueError("explicit task_id does not match request.task_id")
            request = explicit_request
        return self._dispatcher.request_with_execution(request, execute=self._execute)

    def _execute(
        self,
        provider: ModelProvider,
        request: ModelRequest,
        _late_completion: Callable[[ModelResponse | BaseException], None],
    ) -> ModelResponse:
        # HostProcessExecutor uses a bounded subprocess and therefore has no
        # unkillable late completion to reconcile.  The third callback is
        # accepted because ProviderDispatcher supplies it for every lane.
        return self._executor(provider, request)


class HostProcessExecutor:
    """Invoke a static Host runtime command once, without shell expansion."""

    def __init__(
        self,
        command: tuple[str, ...] | list[str],
        *,
        request_dir: str | Path,
        timeout_seconds: float = 120.0,
    ) -> None:
        if isinstance(command, (str, bytes)) or not command or any(not isinstance(item, str) or not item.strip() for item in command):
            raise ValueError("Host runtime command must be a non-empty argument sequence")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.command = tuple(item.strip() for item in command)
        self.request_dir = Path(request_dir).resolve()
        self.timeout_seconds = float(timeout_seconds)

    def __call__(self, provider: ModelProvider, request: ModelRequest) -> ModelResponse:
        dispatch = HostProviderDispatch(provider, execution_boundary="host_process")
        digest = request.metadata.get("egress_manifest_sha256")
        if not isinstance(digest, str):
            raise ProviderError("Host dispatch requires an egress manifest digest", category="host_configuration", retryable=False)
        envelope = dispatch.envelope(request, egress_manifest_sha256=digest)
        self.request_dir.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex
        request_path = self.request_dir / f"{token}.request.json"
        response_path = self.request_dir / f"{token}.response.json"
        request_path.write_text(json.dumps(envelope.to_dict(), ensure_ascii=False, sort_keys=True), encoding="utf-8")
        try:
            completed = subprocess.run(
                [*self.command, "--request", str(request_path), "--response", str(response_path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=self.timeout_seconds,
            )
            if not response_path.is_file():
                raise ProviderError(
                    "Host provider runtime returned no response",
                    category="reconciliation_required",
                    retryable=False,
                )
            try:
                response_payload = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProviderError(
                    "Host provider runtime response is invalid",
                    category="reconciliation_required",
                    retryable=False,
                ) from exc
            if not isinstance(response_payload, dict):
                raise ProviderError("Host provider runtime response is invalid", category="reconciliation_required", retryable=False)
            if response_payload.get("status") != "completed":
                category = response_payload.get("category")
                if not isinstance(category, str) or not category.strip():
                    category = "host_configuration" if completed.returncode != 0 else "provider_http"
                reconciliation_required = response_payload.get("reconciliation_required") is True
                failure_category = "reconciliation_required" if reconciliation_required else category
                failure = ProviderError(
                    "Host provider runtime rejected the dispatch",
                    category=failure_category,
                    retryable=response_payload.get("retryable") is True,
                    failover_safe=response_payload.get("failover_safe") is True,
                    http_status=response_payload.get("http_status") if isinstance(response_payload.get("http_status"), int) else None,
                )
                if reconciliation_required and category != failure_category:
                    # Keep the bounded Host diagnostic without allowing an
                    # unexpected child failure to weaken the UNKNOWN policy.
                    setattr(failure, "host_failure_category", category)
                exception_type = response_payload.get("exception_type")
                if isinstance(exception_type, str) and _SAFE_DIAGNOSTIC_TYPE.fullmatch(exception_type):
                    # The child may expose only a bounded exception type; raw
                    # messages and tracebacks must never cross this boundary.
                    setattr(failure, "host_failure_type", exception_type)
                preserved = response_payload.get("transport_failure_category")
                if isinstance(preserved, str):
                    try:
                        TransportFailureCategory(preserved)
                    except ValueError:
                        pass
                    else:
                        setattr(failure, "transport_failure_category", preserved)
                raise failure
            response = response_payload.get("response")
            if not isinstance(response, dict):
                raise ProviderError("Host provider runtime response is missing", category="reconciliation_required", retryable=False)
            try:
                return ModelResponse.from_dict(response)
            except (TypeError, ValueError) as exc:
                raise ProviderError("Host provider runtime response is malformed", category="reconciliation_required", retryable=False) from exc
        except subprocess.TimeoutExpired as exc:
            raise ProviderError("Host provider runtime timed out", category="reconciliation_required", retryable=False) from exc
        finally:
            for path in (request_path, response_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass


def route_through_host(
    provider: ModelProvider,
    executor: Callable[[ModelProvider, ModelRequest], ModelResponse],
) -> HostProviderDispatch | HostRoutedDispatcher:
    """Return the smallest Host boundary for a concrete or routed provider.

    A canonical ``ProviderDispatcher`` keeps ownership of selection,
    reservation, failover, and reconciliation through its
    ``request_with_execution`` hook.  A concrete provider only needs the
    binding-scoped ``HostProviderDispatch`` wrapper.  Keeping this choice in
    one public helper prevents Planner, Reviewer, Critic, and Worker callers
    from growing subtly different outbound compositions.
    """

    if callable(getattr(provider, "request_with_execution", None)):
        return HostRoutedDispatcher(provider, executor)
    return HostProviderDispatch(provider, execution_boundary="host_process", executor=executor)


__all__ = [
    "HostDispatchEnvelope",
    "HostProcessExecutor",
    "HostProviderDispatch",
    "HostRoutedDispatcher",
    "route_through_host",
]
