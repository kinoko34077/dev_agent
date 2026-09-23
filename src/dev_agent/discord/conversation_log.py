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

    def list_archive_candidates(self, *, cutoff: str, per_channel_limit: int) -> tuple[ConversationMessage, ...]:
        return tuple(
            self._store.list_discord_conversation_archive_candidates(
                cutoff=cutoff,
                per_channel_limit=per_channel_limit,
            )
        )

    def delete(self, message_ids: list[str]) -> int:
        return self._store.delete_discord_conversation_messages(message_ids)


__all__ = ["ConversationLog", "ConversationMessage"]
