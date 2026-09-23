"""Discord HumanInteractionPort adapter without execution or approval authority."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from ..human import HumanInteractionPort, HumanRequest, HumanResponse
from .auth import DiscordAuthorizer
from .binding import SQLiteDiscordBindingStore
from .renderer import render_human_request


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DiscordHumanAdapter:
    """Routes Human authority through Discord while Core remains authoritative."""

    def __init__(
        self,
        port: HumanInteractionPort,
        *,
        authorizer: DiscordAuthorizer,
        deliver: Callable[[HumanRequest, str], Any] | None = None,
        bindings: SQLiteDiscordBindingStore | None = None,
    ) -> None:
        required = ("request_human", "get_request", "record_response", "poll_response", "consume_response")
        if any(not callable(getattr(port, name, None)) for name in required):
            raise TypeError("port does not implement HumanInteractionPort")
        if not isinstance(authorizer, DiscordAuthorizer):
            raise TypeError("authorizer must be DiscordAuthorizer")
        if deliver is not None and not callable(deliver):
            raise TypeError("deliver must be callable")
        self._port = port
        self._authorizer = authorizer
        self._deliver = deliver
        self._bindings = bindings

    def request_human(self, request: HumanRequest) -> str:
        self._port.request_human(request)
        rendered = render_human_request(request)
        if self._deliver is not None:
            self._deliver(request, rendered)
        return rendered

    def poll_response(self, request_id: str) -> HumanResponse | None:
        return self._port.poll_response(request_id)

    def consume_response(self, request_id: str) -> HumanResponse:
        return self._port.consume_response(request_id)

    def record_delivery(self, *, request_id: str, discord_message_id: str) -> bool:
        """Persist only Discord delivery metadata through the existing state store."""
        if self._bindings is None:
            raise RuntimeError("Discord delivery persistence is not configured")
        return self._bindings.record_delivery(request_id, discord_message_id)

    def receive_response(
        self,
        *,
        request_id: str,
        author_id: str,
        response: Any,
        decision: str | None = None,
        guild_id: str = "",
        channel_id: str = "",
        received_at: str | None = None,
    ) -> HumanResponse:
        authorized = (
            self._authorizer.is_user_allowed(author_id)
            if not guild_id and not channel_id
            else self._authorizer.is_allowed(author_id, guild_id, channel_id)
        )
        if not authorized:
            raise PermissionError("Discord identity is not authorized for Human authority")
        request = self._port.get_request(request_id)
        if request is None:
            raise KeyError(request_id)
        selected_decision = decision
        if request.allowed_answers:
            selected_decision = selected_decision or (response.strip() if isinstance(response, str) else "")
            if selected_decision not in request.allowed_answers:
                raise ValueError("decision is not an allowed answer")
        else:
            selected_decision = "TEXT_RESPONSE"
        human_response = HumanResponse(
            request_id=request.request_id,
            responder=f"discord:{author_id}",
            response=response,
            decision=selected_decision,
            received_at=received_at or _now(),
        )
        self._port.record_response(human_response)
        return human_response


__all__ = ["DiscordHumanAdapter"]
