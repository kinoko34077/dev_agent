"""Read-only Discord projections for existing dev_agent state.

This module deliberately does not claim Tasks, wake queues, retry work, or
consume Human/Approval authority.  It observes the existing durable Core
records and delivers bounded projections with the existing Discord delivery
metadata as the idempotency boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from ..domain.protocol import TaskStatus
from ..human import HumanRequest
from .binding import (
    DiscordBinding,
    InMemoryDiscordBindingStore,
    SQLiteDiscordBindingStore,
)
from .renderer import render_human_request, render_progress


SendCallback = Callable[[DiscordBinding, str, Any | None], Awaitable[Any] | Any]
ApprovalViewFactory = Callable[[str, DiscordBinding], Awaitable[Any] | Any]
HumanRequestViewFactory = Callable[[HumanRequest, DiscordBinding], Awaitable[Any] | Any]


def _message_id(value: Any) -> str:
    candidate = getattr(value, "id", value)
    if isinstance(candidate, int) and not isinstance(candidate, bool):
        candidate = str(candidate)
    if not isinstance(candidate, str) or not candidate.isdecimal():
        raise ValueError("Discord sender must return a numeric message id")
    return candidate


def _progress_delivery_key(root_id: str, marker: str) -> str:
    digest = hashlib.sha256(marker.encode("utf-8", errors="replace")).hexdigest()[:32]
    return f"progress:{root_id}:{digest}"


def _event_stage(status: str, event: Mapping[str, Any] | None) -> tuple[str, str | None]:
    event_type = event.get("event_type") if isinstance(event, Mapping) else None
    if event_type == "task.waiting_human":
        return "waiting_human", None
    if event_type == "task.waiting_reconciliation":
        return "reconciliation", None
    if event_type == "task.completed":
        return "completed", None
    if event_type == "task.waiting_approval":
        return "waiting_human", "承認が必要です"
    if event_type == "task.failed":
        return "failed", "作業が失敗しました。状態を確認してください"
    if status == TaskStatus.QUEUED.value:
        return "accepted", None
    if status == TaskStatus.PLANNING.value:
        return "planning", None
    if status in {TaskStatus.READY.value, TaskStatus.RUNNING.value}:
        return "planning", f"現在の状態: {status}"
    if status == TaskStatus.WAITING_APPROVAL.value:
        return "waiting_human", "承認が必要です"
    if status == TaskStatus.WAITING_HUMAN.value:
        return "waiting_human", None
    if status == TaskStatus.WAITING_RECONCILIATION.value:
        return "reconciliation", None
    if status == TaskStatus.COMPLETED.value:
        return "completed", None
    if status in {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
        return "failed", f"現在の状態: {status}"
    return "accepted", f"現在の状態: {status}"


class DiscordOutboundPublisher:
    """Project durable Core state to Discord without owning execution."""

    def __init__(
        self,
        store: Any,
        bindings: SQLiteDiscordBindingStore | InMemoryDiscordBindingStore,
        *,
        send: SendCallback,
        approval_view_factory: ApprovalViewFactory | None = None,
        human_request_view_factory: HumanRequestViewFactory | None = None,
    ) -> None:
        required_store = (
            "list_pending_human_requests",
            "load_task",
            "latest_event_for_task",
        )
        if any(not callable(getattr(store, name, None)) for name in required_store):
            raise TypeError("store does not implement the outbound projection contract")
        required_bindings = ("list_bindings", "find_for_task", "has_any_delivery", "record_delivery")
        if any(not callable(getattr(bindings, name, None)) for name in required_bindings):
            raise TypeError("bindings do not implement the outbound projection contract")
        if not callable(send):
            raise TypeError("send must be callable")
        if approval_view_factory is not None and not callable(approval_view_factory):
            raise TypeError("approval_view_factory must be callable")
        if human_request_view_factory is not None and not callable(human_request_view_factory):
            raise TypeError("human_request_view_factory must be callable")
        self._store = store
        self._bindings = bindings
        self._send = send
        self._approval_view_factory = approval_view_factory
        self._human_request_view_factory = human_request_view_factory
        self._last_error_category: str | None = None

    @property
    def last_error_category(self) -> str | None:
        return self._last_error_category

    def health_projection(self) -> dict[str, str | None]:
        return {
            "state": "DEGRADED" if self._last_error_category else "READY",
            "last_error_category": self._last_error_category,
        }

    def _record_error(self, error: BaseException) -> None:
        self._last_error_category = type(error).__name__[:64]

    async def _send_once(
        self,
        binding: DiscordBinding,
        delivery_key: str,
        content: str,
        *,
        view: Any | None = None,
    ) -> bool:
        if self._bindings.has_any_delivery(delivery_key):
            return False
        result = self._send(binding, content, view)
        if inspect.isawaitable(result):
            result = await result
        message_id = _message_id(result)
        self._bindings.record_delivery(delivery_key, message_id)
        return True

    def _binding_for_task(self, task_id: str) -> DiscordBinding | None:
        return self._bindings.find_for_task(task_id)

    def _latest_for_binding(self, binding: DiscordBinding) -> tuple[Any, Mapping[str, Any] | None] | None:
        candidates: list[tuple[Any, Mapping[str, Any] | None]] = []
        for task_id in dict.fromkeys((binding.run_id, binding.root_id)):
            task = self._store.load_task(task_id)
            if task is None:
                continue
            event = self._store.latest_event_for_task(task_id)
            candidates.append((task, event))
        if not candidates:
            return None
        # The run pointer is the most specific pointer and is intentionally
        # preferred when both root and run records are present.
        return candidates[0]

    async def publish_human_requests(self) -> int:
        published = 0
        for request in self._store.list_pending_human_requests():
            if not isinstance(request, HumanRequest):
                continue
            binding = self._binding_for_task(request.root_id) or self._binding_for_task(request.task_id)
            if binding is None:
                continue
            view = None
            if self._human_request_view_factory is not None:
                view = self._human_request_view_factory(request, binding)
                if inspect.isawaitable(view):
                    view = await view
            if await self._send_once(
                binding,
                request.request_id,
                render_human_request(request),
                view=view,
            ):
                published += 1
        return published

    async def publish_progress(self) -> int:
        published = 0
        for binding in self._bindings.list_bindings():
            latest = self._latest_for_binding(binding)
            if latest is None:
                continue
            task, event = latest
            payload = event.get("payload") if isinstance(event, Mapping) else {}
            approval_reference = payload.get("approval_reference") if isinstance(payload, Mapping) else None
            if (
                task.status is TaskStatus.WAITING_APPROVAL
                and isinstance(approval_reference, str)
                and approval_reference.strip()
                and self._approval_view_factory is not None
            ):
                # The approval projection below carries the actionable button.
                continue
            stage, detail = _event_stage(task.status.value, event)
            event_id = event.get("event_id") if isinstance(event, Mapping) else None
            marker = event_id if isinstance(event_id, str) and event_id else task.updated_at
            delivery_key = _progress_delivery_key(binding.root_id, marker)
            if await self._send_once(binding, delivery_key, render_progress(stage, detail=detail)):
                published += 1
        return published

    async def publish_approvals(self) -> int:
        if self._approval_view_factory is None:
            return 0
        published = 0
        for binding in self._bindings.list_bindings():
            latest = self._latest_for_binding(binding)
            if latest is None:
                continue
            task, event = latest
            if task.status is not TaskStatus.WAITING_APPROVAL or not isinstance(event, Mapping):
                continue
            payload = event.get("payload")
            approval_id = payload.get("approval_reference") if isinstance(payload, Mapping) else None
            if not isinstance(approval_id, str) or not approval_id.strip():
                continue
            view = self._approval_view_factory(approval_id.strip(), binding)
            if inspect.isawaitable(view):
                view = await view
            content = render_progress("waiting_human", detail=f"承認ID: {approval_id.strip()}")
            if await self._send_once(binding, f"approval:{approval_id.strip()}", content, view=view):
                published += 1
        return published

    async def publish_once(self) -> dict[str, int]:
        """Publish one bounded projection pass; no Core work is claimed."""

        human_requests = await self.publish_human_requests()
        approvals = await self.publish_approvals()
        progress = await self.publish_progress()
        return {
            "human_requests": human_requests,
            "approvals": approvals,
            "progress": progress,
        }

    async def serve(
        self,
        *,
        stop: Callable[[], bool],
        interval_seconds: float = 2.0,
    ) -> None:
        if not callable(stop):
            raise TypeError("stop must be callable")
        if isinstance(interval_seconds, bool) or interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        while not stop():
            try:
                await self.publish_once()
                self._last_error_category = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # A transient Discord/StateStore observation failure must not
                # terminate the projection loop or affect Core execution.
                # The next bounded pass will retry observation naturally.
                self._record_error(exc)
            await asyncio.sleep(interval_seconds)


__all__ = ["DiscordOutboundPublisher"]
