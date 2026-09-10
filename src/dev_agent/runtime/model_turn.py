"""Provider request execution for one bounded model turn."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextvars import copy_context
from threading import Event, RLock
from time import time
from typing import Any, Callable

from ..domain.protocol import ModelRequest, ModelResponse
from ..providers.base import ModelProvider


class ProviderRequestCancelled(Exception):
    """A provider request stopped after cancellation was requested."""

    def __init__(self, *, unable_to_confirm: bool) -> None:
        super().__init__("provider request cancellation")
        self.unable_to_confirm = unable_to_confirm


class ProviderExecutionSaturated(RuntimeError):
    """The bounded executor still owns an unkillable timed-out call."""

    is_provider_execution_saturated = True

    def __init__(self, message: str, *, binding_id: str | None = None) -> None:
        super().__init__(message)
        if binding_id is not None and (not isinstance(binding_id, str) or not binding_id.strip()):
            raise ValueError("binding_id must be a non-empty string or None")
        self.binding_id = binding_id.strip() if isinstance(binding_id, str) else None


class ModelTurnExecutor:
    """Run one provider request without owning task-state transitions.

    The executor deliberately keeps the one-thread-per-call boundary used by
    the legacy runtime.  A running Python thread cannot be killed safely, so
    callers must classify a timeout or ambiguous cancellation as an external
    outcome and reconcile it at the higher lifecycle boundary.
    """

    def __init__(
        self,
        provider: ModelProvider,
        *,
        lease_guard: Callable[[], None],
        max_orphaned_requests: int = 1,
        on_capacity_available: Callable[[], None] | None = None,
        binding_id: str | None = None,
    ) -> None:
        if isinstance(max_orphaned_requests, bool) or not isinstance(max_orphaned_requests, int) or max_orphaned_requests < 0:
            raise ValueError("max_orphaned_requests must be a non-negative integer")
        self._provider = provider
        self._lease_guard = lease_guard
        if binding_id is not None and (not isinstance(binding_id, str) or not binding_id.strip()):
            raise ValueError("binding_id must be a non-empty string or None")
        self._binding_id = binding_id.strip() if isinstance(binding_id, str) else None
        self._max_orphaned_requests = max_orphaned_requests
        self._orphaned_requests: set[object] = set()
        self._orphan_callbacks: dict[object, Callable[[ModelResponse | BaseException], None]] = {}
        self._orphan_lock = RLock()
        if on_capacity_available is not None and not callable(on_capacity_available):
            raise TypeError("on_capacity_available must be callable or None")
        self._on_capacity_available = on_capacity_available

    @property
    def orphaned_requests(self) -> int:
        with self._orphan_lock:
            return len(self._orphaned_requests)

    def _release_orphan(self, future: object) -> None:
        released = False
        callback: Callable[[ModelResponse | BaseException], None] | None = None
        with self._orphan_lock:
            if future in self._orphaned_requests:
                self._orphaned_requests.discard(future)
                callback = self._orphan_callbacks.pop(future, None)
                released = True
        if released and callback is not None:
            self._notify_late_completion(future, callback)
        if released and self._on_capacity_available is not None:
            # A wake callback is a liveness hint only.  A late provider
            # completion must never become a runtime failure because Queue or
            # another process is temporarily unavailable.
            try:
                self._on_capacity_available()
            except Exception:
                pass

    @staticmethod
    def _notify_late_completion(
        future: Any,
        callback: Callable[[ModelResponse | BaseException], None],
    ) -> None:
        """Deliver a late result without allowing callback failures to leak.

        The callback runs outside the executor lock and is best-effort.  It is
        a reconciliation hook, not part of the provider call's original
        success path; a broken wake/reconciliation observer must not turn a
        completed provider Future into an unbounded worker failure.
        """

        try:
            outcome: ModelResponse | BaseException = future.result()
        except BaseException as exc:
            outcome = exc
        try:
            callback(outcome)
        except Exception:
            pass

    def _track_if_running(
        self,
        future: Any,
        on_late_completion: Callable[[ModelResponse | BaseException], None] | None = None,
    ) -> bool:
        """Retain a Future while its provider call may still be executing.

        ``Future.cancel()`` only succeeds before a worker thread starts.  Both
        timeout and cancellation therefore need the same best-effort tracking
        path; otherwise an unconfirmed cancellation can leave an unbounded
        number of provider threads outside the saturation guard.
        """

        if future.done():
            return False
        with self._orphan_lock:
            # Re-check under the lock because a provider can finish between
            # the first check and insertion into the orphan set.
            if future.done():
                return False
            self._orphaned_requests.add(future)
            if on_late_completion is not None:
                self._orphan_callbacks[future] = on_late_completion
            if future.done():
                self._orphaned_requests.discard(future)
                self._orphan_callbacks.pop(future, None)
                return False
        return True

    def _cancel_or_track(
        self,
        future: Any,
        on_late_completion: Callable[[ModelResponse | BaseException], None] | None = None,
    ) -> bool:
        """Cancel a not-yet-running call or track its still-running Future."""

        cancelled = future.cancel()
        if not cancelled:
            self._track_if_running(future, on_late_completion)
        return cancelled

    def request(
        self,
        request: ModelRequest,
        deadline_epoch: float,
        cancel_event: Event,
        *,
        on_late_completion: Callable[[ModelResponse | BaseException], None] | None = None,
    ) -> ModelResponse:
        if on_late_completion is not None and not callable(on_late_completion):
            raise TypeError("on_late_completion must be callable or None")
        self._lease_guard()
        with self._orphan_lock:
            if len(self._orphaned_requests) >= self._max_orphaned_requests:
                raise ProviderExecutionSaturated(
                    "a previous provider request is still running after timeout",
                    binding_id=self._binding_id,
                )
        executor = ThreadPoolExecutor(max_workers=1)
        # ProviderDispatcher may consult the run's lease proof from inside
        # its provider thread. ContextVars do not propagate through a new
        # thread implicitly, so capture the immutable run context explicitly.
        provider_context = copy_context()
        future = executor.submit(provider_context.run, self._provider.request, request)
        future.add_done_callback(self._release_orphan)
        try:
            while True:
                remaining = deadline_epoch - time()
                if remaining <= 0:
                    if cancel_event.is_set():
                        # The deadline and an operator cancellation can race.
                        # Preserve the cancellation state instead of turning
                        # an in-flight provider into an ordinary timeout.
                        cancelled = self._cancel_or_track(future, on_late_completion)
                        raise ProviderRequestCancelled(unable_to_confirm=not cancelled)
                    # Best effort; arbitrary provider threads are not
                    # killable.  Keep an unconfirmed call inside the bounded
                    # orphan guard until its Future callback releases it.
                    tracked = self._cancel_or_track(future, on_late_completion)
                    if not tracked and future.done() and on_late_completion is not None:
                        self._notify_late_completion(future, on_late_completion)
                    raise FutureTimeoutError()
                try:
                    response = future.result(timeout=min(0.05, remaining))
                    # A provider can complete concurrently with the deadline
                    # boundary. Do not accept a response that arrived after
                    # the runtime lease expired: the caller must reconcile it.
                    if time() >= deadline_epoch:
                        raise FutureTimeoutError()
                    return response
                except FutureTimeoutError:
                    if cancel_event.is_set():
                        if future.done():
                            return future.result(timeout=0)
                        cancelled = self._cancel_or_track(future, on_late_completion)
                        raise ProviderRequestCancelled(unable_to_confirm=not cancelled)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


__all__ = ["ModelTurnExecutor", "ProviderExecutionSaturated", "ProviderRequestCancelled"]
