"""Thin Discord-to-Core composition boundary.

This module only classifies already-validated ingress and invokes callbacks
owned by Operation or Process Coordination. It has no task, queue, retry, or
model state of its own.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .adapter import DiscordIngressEvent, DiscordMessageKind
from .intent import IntentProposal


class DiscordCoreAdapter:
    """Delegate Discord input to the existing Core public boundaries."""

    def __init__(
        self,
        *,
        submit_request: Callable[[str, DiscordIngressEvent], Any] | None = None,
        submit_coordination: Callable[[str, str, DiscordIngressEvent], Any] | None = None,
        submit_wait: Callable[[str, DiscordIngressEvent, IntentProposal], Any] | None = None,
        read_status: Callable[[DiscordIngressEvent], Mapping[str, Any] | str | None] | None = None,
        chat_response: Callable[[DiscordIngressEvent], Mapping[str, Any] | str | None] | None = None,
    ) -> None:
        for callback, name in (
            (submit_request, "submit_request"),
            (submit_coordination, "submit_coordination"),
            (submit_wait, "submit_wait"),
            (read_status, "read_status"),
            (chat_response, "chat_response"),
        ):
            if callback is not None and not callable(callback):
                raise TypeError(f"{name} must be callable")
        self._submit_request = submit_request
        self._submit_coordination = submit_coordination
        self._submit_wait = submit_wait
        self._read_status = read_status
        self._chat_response = chat_response

    def handle(self, event: DiscordIngressEvent) -> Any:
        if not isinstance(event, DiscordIngressEvent):
            raise TypeError("event must be DiscordIngressEvent")
        if event.kind is DiscordMessageKind.READ_QUERY:
            if self._read_status is None:
                raise RuntimeError("Discord read query has no Core status boundary")
            return self._read_status(event)
        if event.kind is DiscordMessageKind.CHAT:
            # CHAT is deliberately not an Operation.  It is a bounded UI
            # projection and carries no mutation authority.
            if self._chat_response is not None:
                result = self._chat_response(event)
                if result is not None:
                    return result
            return {"state": "CHAT", "text": "会話の参照情報を取得できませんでした。"}
        if event.kind is DiscordMessageKind.WAIT:
            if self._submit_wait is None or event.intent_proposal is None:
                raise RuntimeError("Discord wait has no Core durable wait boundary")
            return self._submit_wait(event.message.content, event, event.intent_proposal)
        if event.kind is DiscordMessageKind.NEW_REQUEST:
            if self._submit_request is None:
                raise RuntimeError("Discord request has no Core Operation boundary")
            return self._submit_request(event.message.content, event)
        if self._submit_coordination is None:
            raise RuntimeError("Discord intervention has no Core Coordination boundary")
        return self._submit_coordination(event.kind.value, event.message.content, event)


__all__ = ["DiscordCoreAdapter"]
