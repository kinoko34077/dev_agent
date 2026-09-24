"""Bounded, read-only Discord conversation context for Core ingress."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..security.audit import AuditRecorder
from .auth import DiscordAuthorizer


_DEFAULT_LIMIT = 20
_DEFAULT_MAX_CHARS = 8_000


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class DiscordHistoryMessage:
    """A sanitized context item; metadata is context, never replay authority."""

    role: str
    content: str
    # Metadata enriches Core context without changing the legacy value
    # semantics of a history item (role + content).  Keeping it out of
    # dataclass equality preserves callers that compare the original bounded
    # two-field projection while consumers that need the richer context can
    # read the explicit attributes or ``to_dict(include_metadata=True)``.
    message_id: str | None = field(default=None, compare=False)
    created_at: str | None = field(default=None, compare=False)
    speaker_id: str | None = field(default=None, compare=False)
    speaker_name: str | None = field(default=None, compare=False)
    reply_to_message_id: str | None = field(default=None, compare=False)
    message_kind: str | None = field(default=None, compare=False)
    root_id: str | None = field(default=None, compare=False)
    run_id: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if self.role not in {"human", "assistant"}:
            raise ValueError("role must be human or assistant")
        if not isinstance(self.content, str) or not self.content:
            raise ValueError("content must be non-empty text")
        if len(self.content) > _DEFAULT_MAX_CHARS:
            raise ValueError("content exceeds the bounded history size")
        for name in ("message_id", "reply_to_message_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.isdecimal() or len(value) > 32):
                raise ValueError(f"{name} must be a bounded Discord ID")
        for name in ("created_at", "speaker_id", "speaker_name", "message_kind", "root_id", "run_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 256):
                raise ValueError(f"{name} must be bounded text")

    def to_dict(self, *, include_metadata: bool = False) -> dict[str, str | None]:
        result: dict[str, str | None] = {"role": self.role, "content": self.content}
        if include_metadata:
            for name in (
                "message_id", "created_at", "speaker_id", "speaker_name",
                "reply_to_message_id", "message_kind", "root_id", "run_id",
            ):
                value = getattr(self, name)
                if value is not None:
                    result[name] = value
        return result


def _message_channel_id(message: Any) -> str | None:
    channel = getattr(message, "channel", None)
    value = getattr(channel, "id", None)
    if value is None:
        return None
    return str(value)


def _safe_content(message: Any, *, remaining: int) -> str:
    raw = getattr(message, "content", "")
    if not isinstance(raw, str) or not raw.strip() or remaining <= 0:
        return ""
    safe = AuditRecorder.sanitize_payload({"content": raw}).get("content", "")
    if not isinstance(safe, str) or not safe:
        return ""
    # Preserve chronological items while making the final item fit the
    # aggregate bound.  No raw Discord payload is retained.
    return safe[:remaining]


async def collect_discord_history(
    channel: Any,
    *,
    current_message_id: str,
    authorizer: DiscordAuthorizer,
    guild_id: str,
    channel_id: str,
    bot_user_id: str,
    thread_id: str = "",
    current_message: Any | None = None,
    limit: int = _DEFAULT_LIMIT,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> tuple[DiscordHistoryMessage, ...]:
    """Read bounded context from one Discord channel or thread.

    The result is context only.  This function never calls ingress, writes a
    mailbox, creates a Task, or persists the Discord history.
    """

    if channel is None:
        raise TypeError("channel is required")
    if not isinstance(authorizer, DiscordAuthorizer):
        raise TypeError("authorizer must be DiscordAuthorizer")
    if not isinstance(current_message_id, str) or not current_message_id.strip().isdecimal():
        raise ValueError("current_message_id must be a Discord numeric ID")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= _DEFAULT_LIMIT:
        raise ValueError("limit must be between 1 and 20")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or not 1 <= max_chars <= _DEFAULT_MAX_CHARS:
        raise ValueError("max_chars must be between 1 and 8000")

    location_id = str(thread_id or channel_id)
    history_kwargs: dict[str, object] = {"limit": limit, "oldest_first": True}
    if current_message is not None:
        history_kwargs["before"] = current_message

    result: list[DiscordHistoryMessage] = []
    remaining = max_chars
    async for item in channel.history(**history_kwargs):
        if len(result) >= limit or remaining <= 0:
            break
        item_id = str(getattr(item, "id", ""))
        if not item_id or item_id == current_message_id.strip():
            continue
        if _message_channel_id(item) != location_id:
            continue

        author = getattr(item, "author", None)
        author_id = str(getattr(author, "id", ""))
        if not author_id or getattr(item, "webhook_id", None) is not None:
            continue
        is_bot = bool(getattr(author, "bot", False))
        if is_bot:
            if author_id != str(bot_user_id):
                continue
            role = "assistant"
        else:
            if not authorizer.is_allowed(author_id, str(guild_id), str(channel_id)):
                continue
            role = "human"

        content = _safe_content(item, remaining=remaining)
        if not content:
            continue
        reference = getattr(item, "reference", None)
        reference_id = str(getattr(reference, "message_id", ""))
        result.append(
            DiscordHistoryMessage(
                role=role,
                content=content,
                message_id=item_id,
                created_at=_iso(getattr(item, "created_at", None)),
                speaker_id=author_id,
                speaker_name=str(getattr(author, "name", author_id))[:256] or author_id,
                reply_to_message_id=reference_id if reference_id.isdecimal() else None,
                message_kind="HISTORY_CONTEXT",
            )
        )
        remaining -= len(content)

    return tuple(result)


__all__ = ["DiscordHistoryMessage", "collect_discord_history"]
