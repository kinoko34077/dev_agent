"""Discord-facing facade for the neutral durable conversation record."""

from __future__ import annotations

from typing import Any

from ..state.conversation import ConversationMessage


class ConversationLog:
    """Small facade over the existing SQLiteStateStore conversation methods."""

    def __init__(self, store: Any) -> None:
        required = ("append_discord_conversation_message", "list_discord_conversation_messages")
        if any(not callable(getattr(store, name, None)) for name in required):
            raise TypeError("store does not implement the conversation log contract")
        self._store = store

    def append(self, message: ConversationMessage) -> bool:
        if not isinstance(message, ConversationMessage):
            raise TypeError("message must be a ConversationMessage")
        return self._store.append_discord_conversation_message(message)

    def list_context(
        self,
        binding_key: str,
        *,
        limit: int = 50,
        max_chars: int = 16_000,
    ) -> tuple[ConversationMessage, ...]:
        return tuple(self._store.list_discord_conversation_messages(binding_key, limit=limit, max_chars=max_chars))


__all__ = ["ConversationLog", "ConversationMessage"]
