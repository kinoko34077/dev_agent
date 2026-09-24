"""Local conversation context and reconciliation-only Discord history sync."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from .auth import DiscordAuthorizer
from .binding import DiscordBindingKey
from .conversation_log import ConversationLog, ConversationMessage
from .history import DiscordHistoryMessage


def _binding_key(key: DiscordBindingKey) -> str:
    return "|".join((key.guild_id, key.channel_id, key.thread_id))


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else datetime.now(timezone.utc).isoformat()


def _reply_reference_id(message: Any) -> str | None:
    """Return only a bounded Discord message-reference ID."""

    reference = getattr(message, "reference", None)
    candidate = getattr(reference, "message_id", None) if reference is not None else None
    value = str(candidate) if candidate is not None else ""
    return value if value.isdecimal() and len(value) <= 32 else None


def build_bounded_context(
    log: ConversationLog,
    binding_key: DiscordBindingKey,
    *,
    current_message_id: str,
    limit: int = 50,
    max_chars: int = 16_000,
    reply_to_message_id: str | None = None,
    reply_depth: int = 3,
) -> tuple[DiscordHistoryMessage, ...]:
    """Project local log rows into bounded Core context without replay."""

    if not isinstance(log, ConversationLog):
        raise TypeError("log must be a ConversationLog")
    if not isinstance(binding_key, DiscordBindingKey):
        raise TypeError("binding_key must be a DiscordBindingKey")
    if not isinstance(current_message_id, str) or not current_message_id.strip():
        raise ValueError("current_message_id must be non-empty text")
    if isinstance(reply_depth, bool) or not isinstance(reply_depth, int) or not 0 <= reply_depth <= 3:
        raise ValueError("reply_depth must be between 0 and 3")
    rows = log.list_context(
        _binding_key(binding_key),
        limit=limit,
        max_chars=max_chars,
        newest_first=True,
    )
    selected = list(rows)
    current_reply = (
        reply_to_message_id.strip()
        if isinstance(reply_to_message_id, str) and reply_to_message_id.strip().isdecimal()
        else None
    )
    seen = {row.message_id for row in selected}
    for _ in range(reply_depth):
        if current_reply is None or current_reply in seen:
            break
        row = log.get(current_reply)
        if row is None or row.speaker_role not in {"human", "assistant"}:
            break
        selected.append(row)
        seen.add(row.message_id)
        current_reply = row.reply_to_message_id
    selected.sort(key=lambda row: (row.created_at, row.received_at, row.message_id))
    result: list[DiscordHistoryMessage] = []
    remaining = max_chars
    for row in selected:
        if row.message_id == current_message_id.strip():
            continue
        if row.speaker_role not in {"human", "assistant"}:
            continue
        if len(row.content) > remaining:
            continue
        result.append(
            DiscordHistoryMessage(
                role=row.speaker_role,
                content=row.content,
                message_id=row.message_id,
                created_at=row.created_at,
                speaker_id=row.speaker_id,
                speaker_name=row.speaker_name,
                reply_to_message_id=row.reply_to_message_id,
                message_kind=row.message_kind,
                root_id=row.root_id,
                run_id=row.run_id,
            )
        )
        remaining -= len(row.content)
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
    reply_to_message_id: str | None = None,
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
        reply_to_message_id=(
            reply_to_message_id
            if isinstance(reply_to_message_id, str) and reply_to_message_id.isdecimal() and len(reply_to_message_id) <= 32
            else _reply_reference_id(message)
        ),
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
            reply_to_message_id=_reply_reference_id(item),
            message_kind="HISTORY_SYNC",
            root_id=None,
            run_id=None,
            source="discord_history",
        )
        inserted += int(log.append(message))
    return inserted


async def sync_discord_history(
    channel: Any,
    log: ConversationLog,
    bindings: Any,
    binding_key: DiscordBindingKey,
    *,
    authorizer: DiscordAuthorizer,
    bot_user_id: str,
    current_message_id: str,
    current_message: Any | None = None,
    history_after_factory: Callable[[int], Any] | None = None,
    seed_limit: int = 500,
    incremental_limit: int = 100,
    seed_days: int = 30,
) -> int:
    """Synchronize bounded Discord history into the existing ConversationLog.

    The durable cursor is only a channel/thread pointer.  Items returned by
    Discord are passed to ``sync_history_once`` and never to ingress, a
    mailbox, or a Task boundary.
    """

    if channel is None:
        raise TypeError("channel is required")
    if not isinstance(log, ConversationLog):
        raise TypeError("log must be a ConversationLog")
    if not isinstance(binding_key, DiscordBindingKey):
        raise TypeError("binding_key must be a DiscordBindingKey")
    if not callable(getattr(bindings, "get_history_sync", None)) or not callable(getattr(bindings, "save_history_sync", None)):
        raise TypeError("bindings must implement the Discord history sync contract")
    if not isinstance(authorizer, DiscordAuthorizer):
        raise TypeError("authorizer must be a DiscordAuthorizer")
    if not isinstance(current_message_id, str) or not current_message_id.strip().isdecimal() or len(current_message_id.strip()) > 32:
        raise ValueError("current_message_id must be a Discord numeric ID")
    if isinstance(seed_limit, bool) or not isinstance(seed_limit, int) or not 1 <= seed_limit <= 500:
        raise ValueError("seed_limit must be between 1 and 500")
    if isinstance(incremental_limit, bool) or not isinstance(incremental_limit, int) or not 1 <= incremental_limit <= 100:
        raise ValueError("incremental_limit must be between 1 and 100")
    if isinstance(seed_days, bool) or not isinstance(seed_days, int) or not 1 <= seed_days <= 3650:
        raise ValueError("seed_days must be between 1 and 3650")
    if history_after_factory is not None and not callable(history_after_factory):
        raise TypeError("history_after_factory must be callable")

    sync_state = bindings.get_history_sync(binding_key)
    is_seed = sync_state is None or not sync_state.seeded
    history_kwargs: dict[str, Any] = {
        "limit": seed_limit if is_seed else incremental_limit,
        "oldest_first": True,
    }
    if is_seed:
        if current_message is not None:
            history_kwargs["before"] = current_message
    elif sync_state.latest_synced_message_id:
        latest_id = int(sync_state.latest_synced_message_id)
        history_kwargs["after"] = history_after_factory(latest_id) if history_after_factory else sync_state.latest_synced_message_id

    items: list[Any] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=seed_days)
    async for item in channel.history(**history_kwargs):
        if is_seed:
            created_at = getattr(item, "created_at", None)
            if isinstance(created_at, datetime):
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                if created_at < cutoff:
                    continue
        items.append(item)

    inserted = sync_history_once(
        items,
        log,
        binding_key,
        authorizer=authorizer,
        bot_user_id=bot_user_id,
        current_message_id=current_message_id,
    )
    ids = [
        str(getattr(item, "id", ""))
        for item in items
        if str(getattr(item, "id", "")).isdecimal() and len(str(getattr(item, "id", ""))) <= 32
    ]
    numeric_ids = [int(item) for item in ids]
    previous_latest = (
        int(sync_state.latest_synced_message_id)
        if sync_state is not None and sync_state.latest_synced_message_id and sync_state.latest_synced_message_id.isdecimal()
        else None
    )
    page_limit = seed_limit if is_seed else incremental_limit
    page_is_full = len(items) >= page_limit
    if numeric_ids:
        latest_value = max(numeric_ids)
        # A full Discord page is not proof that the cursor has caught up.  Do
        # not jump to the current Gateway message; the next invocation must
        # request the next page using the last item actually observed here.
        if not page_is_full:
            latest_value = max(latest_value, int(current_message_id.strip()))
    elif previous_latest is not None:
        latest_value = previous_latest
    else:
        latest_value = int(current_message_id.strip())
    latest = str(latest_value)
    oldest = str(min(numeric_ids)) if is_seed and numeric_ids else (
        sync_state.oldest_seeded_message_id if sync_state is not None else None
    )
    bindings.save_history_sync(
        binding_key,
        latest_synced_message_id=latest,
        oldest_seeded_message_id=oldest,
        seeded=True,
    )
    return inserted


__all__ = ["build_bounded_context", "conversation_message_from_discord", "sync_discord_history", "sync_history_once"]
