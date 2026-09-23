"""Local conversation context and reconciliation-only Discord history sync."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from .auth import DiscordAuthorizer
from .binding import DiscordBindingKey
from .conversation_log import ConversationLog, ConversationMessage
from .history import DiscordHistoryMessage


def _binding_key(key: DiscordBindingKey) -> str:
    return "|".join((key.guild_id, key.channel_id, key.thread_id))


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else datetime.now(timezone.utc).isoformat()


def build_bounded_context(
    log: ConversationLog,
    binding_key: DiscordBindingKey,
    *,
    current_message_id: str,
    limit: int = 50,
    max_chars: int = 16_000,
) -> tuple[DiscordHistoryMessage, ...]:
    """Project local log rows into bounded Core context without replay."""

    if not isinstance(log, ConversationLog):
        raise TypeError("log must be a ConversationLog")
    if not isinstance(binding_key, DiscordBindingKey):
        raise TypeError("binding_key must be a DiscordBindingKey")
    if not isinstance(current_message_id, str) or not current_message_id.strip():
        raise ValueError("current_message_id must be non-empty text")
    rows = log.list_context(_binding_key(binding_key), limit=limit, max_chars=max_chars)
    result: list[DiscordHistoryMessage] = []
    for row in rows:
        if row.message_id == current_message_id.strip():
            continue
        if row.speaker_role not in {"human", "assistant"}:
            continue
        result.append(DiscordHistoryMessage(role=row.speaker_role, content=row.content))
    return tuple(result)


def conversation_message_from_discord(
    message: Any,
    *,
    binding_key: DiscordBindingKey,
    message_kind: str,
    message_id: str,
    content: str,
    direction: str,
    root_id: str | None = None,
    run_id: str | None = None,
) -> ConversationMessage:
    """Create one sanitized log row from an already-authorized Discord event."""

    author = getattr(message, "author", None)
    author_id = str(getattr(author, "id", ""))
    role = "assistant" if direction == "outbound" else "human"
    return ConversationMessage(
        message_id=message_id,
        binding_key=_binding_key(binding_key),
        guild_id=binding_key.guild_id,
        channel_id=binding_key.channel_id,
        thread_id=binding_key.thread_id,
        created_at=_iso(getattr(message, "created_at", None)),
        received_at=datetime.now(timezone.utc).isoformat(),
        speaker_role=role,
        speaker_id=author_id or ("dev_agent" if direction == "outbound" else "unknown"),
        speaker_name=str(getattr(author, "name", "dev_agent" if direction == "outbound" else "Human"))[:64],
        direction=direction,
        content=content,
        reply_to_message_id=None,
        message_kind=message_kind,
        root_id=root_id,
        run_id=run_id,
        source="discord",
    )


def sync_history_once(
    history_messages: Iterable[Any],
    log: ConversationLog,
    binding_key: DiscordBindingKey,
    *,
    authorizer: DiscordAuthorizer,
    bot_user_id: str,
    current_message_id: str,
) -> int:
    """Reconcile API history into the log; never route it into Core ingress."""

    if not isinstance(log, ConversationLog):
        raise TypeError("log must be a ConversationLog")
    if not isinstance(binding_key, DiscordBindingKey):
        raise TypeError("binding_key must be a DiscordBindingKey")
    if not isinstance(authorizer, DiscordAuthorizer):
        raise TypeError("authorizer must be a DiscordAuthorizer")
    if not isinstance(bot_user_id, str) or not bot_user_id.strip().isdecimal():
        raise ValueError("bot_user_id must be a Discord numeric ID")
    if not isinstance(current_message_id, str) or not current_message_id.strip().isdecimal():
        raise ValueError("current_message_id must be a Discord numeric ID")

    binding = _binding_key(binding_key)
    inserted = 0
    for item in history_messages:
        message_id = str(getattr(item, "id", ""))
        if not message_id.isdecimal() or message_id == current_message_id.strip():
            continue
        author = getattr(item, "author", None)
        author_id = str(getattr(author, "id", ""))
        if not author_id.isdecimal() or getattr(item, "webhook_id", None) is not None:
            continue
        is_bot = bool(getattr(author, "bot", False))
        if is_bot:
            if author_id != bot_user_id.strip():
                continue
            role = "assistant"
            direction = "outbound"
        else:
            if not authorizer.is_allowed(author_id, binding_key.guild_id, binding_key.channel_id):
                continue
            role = "human"
            direction = "inbound"
        content = getattr(item, "content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        message = ConversationMessage(
            message_id=message_id,
            binding_key=binding,
            guild_id=binding_key.guild_id,
            channel_id=binding_key.channel_id,
            thread_id=binding_key.thread_id,
            created_at=_iso(getattr(item, "created_at", None)),
            received_at=datetime.now(timezone.utc).isoformat(),
            speaker_role=role,
            speaker_id=author_id,
            speaker_name=str(getattr(author, "name", author_id))[:64] or author_id,
            direction=direction,
            content=content,
            reply_to_message_id=None,
            message_kind="HISTORY_SYNC",
            root_id=None,
            run_id=None,
            source="discord_history",
        )
        inserted += int(log.append(message))
    return inserted


__all__ = ["build_bounded_context", "conversation_message_from_discord", "sync_history_once"]
