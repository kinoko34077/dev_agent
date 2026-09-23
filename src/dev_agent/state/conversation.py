"""Neutral durable record for bounded Discord conversation messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..security.audit import AuditRecorder


_MAX_ID_CHARS = 64
_MAX_CONTENT_CHARS = 4096


def _text(value: Any, name: str, *, max_chars: int = _MAX_ID_CHARS) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    normalized = value.strip()
    if len(normalized) > max_chars:
        raise ValueError(f"{name} exceeds {max_chars} characters")
    return normalized


@dataclass(frozen=True)
class ConversationMessage:
    message_id: str
    binding_key: str
    guild_id: str
    channel_id: str
    thread_id: str
    created_at: str
    received_at: str
    speaker_role: str
    speaker_id: str
    speaker_name: str
    direction: str
    content: str
    reply_to_message_id: str | None
    message_kind: str
    root_id: str | None
    run_id: str | None
    source: str

    def __post_init__(self) -> None:
        for name in (
            "message_id", "binding_key", "guild_id", "channel_id",
            "created_at", "received_at", "speaker_id", "speaker_name",
            "message_kind", "source",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        if not isinstance(self.thread_id, str) or len(self.thread_id) > _MAX_ID_CHARS:
            raise ValueError("thread_id must be bounded text")
        object.__setattr__(self, "thread_id", self.thread_id.strip())
        if self.speaker_role not in {"human", "assistant", "system"}:
            raise ValueError("speaker_role must be human, assistant, or system")
        if self.direction not in {"inbound", "outbound"}:
            raise ValueError("direction must be inbound or outbound")
        safe_content = AuditRecorder.sanitize_payload({"content": self.content}).get("content", "")
        object.__setattr__(self, "content", _text(safe_content, "content", max_chars=_MAX_CONTENT_CHARS))
        if self.reply_to_message_id is not None:
            object.__setattr__(self, "reply_to_message_id", _text(self.reply_to_message_id, "reply_to_message_id"))
        for name in ("root_id", "run_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name))

    def to_storage_tuple(self) -> tuple[str, ... | None]:
        return (
            self.message_id, self.binding_key, self.guild_id, self.channel_id,
            self.thread_id, self.created_at, self.received_at, self.speaker_role,
            self.speaker_id, self.speaker_name, self.direction, self.content,
            self.reply_to_message_id, self.message_kind, self.root_id, self.run_id,
            self.source,
        )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "message_id": self.message_id,
            "binding_key": self.binding_key,
            "guild_id": self.guild_id,
            "channel_id": self.channel_id,
            "thread_id": self.thread_id,
            "created_at": self.created_at,
            "received_at": self.received_at,
            "speaker_role": self.speaker_role,
            "speaker_id": self.speaker_id,
            "speaker_name": self.speaker_name,
            "direction": self.direction,
            "content": self.content,
            "reply_to_message_id": self.reply_to_message_id,
            "message_kind": self.message_kind,
            "root_id": self.root_id,
            "run_id": self.run_id,
            "source": self.source,
        }

    @classmethod
    def from_row(cls, row: Any) -> "ConversationMessage":
        return cls(
            message_id=row["message_id"], binding_key=row["binding_key"],
            guild_id=row["guild_id"], channel_id=row["channel_id"],
            thread_id=row["thread_id"], created_at=row["created_at"],
            received_at=row["received_at"], speaker_role=row["speaker_role"],
            speaker_id=row["speaker_id"], speaker_name=row["speaker_name"],
            direction=row["direction"], content=row["content"],
            reply_to_message_id=row["reply_to_message_id"],
            message_kind=row["message_kind"], root_id=row["root_id"],
            run_id=row["run_id"], source=row["source"],
        )


__all__ = ["ConversationMessage"]
