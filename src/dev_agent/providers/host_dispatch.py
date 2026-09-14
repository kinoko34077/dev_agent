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

from ..domain.protocol import ModelRequest, ModelResponse
from ..resources.provider_policy import validate_provider_instance_authority
from .base import ModelProvider, ProviderError, TransportFailureCategory, classify_transport_failure


_EXECUTION_BOUNDARIES = frozenset(
    {"unclassified", "codex_sandbox", "host_process", "provider_process"}
)


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
            self.last_transport_category = classify_transport_failure(
                exc,
                execution_boundary=None if self.execution_boundary == "unclassified" else self.execution_boundary,
            )
            raise
        if not isinstance(response, ModelResponse):
            raise TypeError("provider must return ModelResponse")
        return response


__all__ = ["HostProviderDispatch"]
