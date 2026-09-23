"""Pure Discord ingress, message classification, and repository scope checks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..coordination.protocol_helpers import validate_relative_path, validate_text
from ..security.protected_paths import PathProtectionClass, classify_path
from .auth import DiscordAuthorizer
from .binding import DiscordBindingKey, InMemoryDiscordBindingStore


class DiscordMessageKind(str, Enum):
    NEW_REQUEST = "NEW_REQUEST"
    READ_QUERY = "READ_QUERY"
    NOTE = "NOTE"
    PARALLEL = "PARALLEL"
    INTERRUPT = "INTERRUPT"
    CANCEL = "CANCEL"


def classify_message(content: str) -> DiscordMessageKind:
    normalized = validate_text(content, "message", max_chars=4_000).strip()
    lowered = normalized.casefold()
    if lowered in {"/status", "status", "今何してる", "進捗", "状態"}:
        return DiscordMessageKind.READ_QUERY
    if lowered == "/cancel" or lowered.startswith("/cancel ") or normalized in {"停止して", "中止して", "キャンセル"}:
        return DiscordMessageKind.CANCEL
    if lowered == "/interrupt" or lowered.startswith("/interrupt ") or normalized.startswith("割り込み"):
        return DiscordMessageKind.INTERRUPT
    if lowered == "/parallel" or lowered.startswith("/parallel ") or normalized.startswith("並行"):
        return DiscordMessageKind.PARALLEL
    if lowered == "/note" or lowered.startswith("/note ") or normalized.startswith(("補足", "メモ")):
        return DiscordMessageKind.NOTE
    return DiscordMessageKind.NEW_REQUEST


@dataclass(frozen=True)
class DiscordMessage:
    message_id: str
    author_id: str
    guild_id: str
    channel_id: str
    thread_id: str
    content: str
    author_is_bot: bool = False

    def __post_init__(self) -> None:
        key = DiscordBindingKey(self.guild_id, self.channel_id, self.thread_id)
        object.__setattr__(self, "guild_id", key.guild_id)
        object.__setattr__(self, "channel_id", key.channel_id)
        object.__setattr__(self, "thread_id", key.thread_id)
        for name, value in (("message_id", self.message_id), ("author_id", self.author_id)):
            if not isinstance(value, str) or not value.strip().isdecimal() or len(value.strip()) > 32:
                raise ValueError(f"{name} must be a bounded Discord numeric ID")
            object.__setattr__(self, name, value.strip())
        if not isinstance(self.content, str) or not self.content.strip() or len(self.content) > 4_000:
            raise ValueError("content must be bounded non-empty text")
        if not isinstance(self.author_is_bot, bool):
            raise ValueError("author_is_bot must be boolean")


@dataclass(frozen=True)
class DiscordIngressEvent:
    message: DiscordMessage
    kind: DiscordMessageKind
    binding_key: DiscordBindingKey


class DiscordScope:
    """Resolve only existing, normal repository-relative scope/reference paths."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir():
            raise ValueError("workspace must be an existing directory")

    def _resolve(self, value: str, *, want_directory: bool) -> str:
        relative = validate_relative_path(value, "Discord path")
        if classify_path(relative) is not PathProtectionClass.NORMAL_REPO:
            raise ValueError("Discord path is protected or authority-sensitive")
        candidate = (self.workspace / relative).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise ValueError("Discord path escapes the workspace")
        if want_directory and not candidate.is_dir():
            raise ValueError("Discord directory does not exist")
        if not want_directory and not candidate.is_file():
            raise ValueError("Discord file does not exist")
        return relative

    def resolve_directory(self, value: str) -> str:
        return self._resolve(value, want_directory=True)

    def resolve_file(self, value: str) -> str:
        return self._resolve(value, want_directory=False)


class DiscordIngressAdapter:
    """Accept authorized, non-duplicate messages without owning execution."""

    def __init__(self, *, authorizer: DiscordAuthorizer, bindings: InMemoryDiscordBindingStore) -> None:
        if not isinstance(authorizer, DiscordAuthorizer):
            raise TypeError("authorizer must be DiscordAuthorizer")
        if not isinstance(bindings, InMemoryDiscordBindingStore):
            raise TypeError("bindings must be InMemoryDiscordBindingStore")
        self._authorizer = authorizer
        self._bindings = bindings

    def accept(self, message: DiscordMessage) -> DiscordIngressEvent | None:
        if not isinstance(message, DiscordMessage) or message.author_is_bot:
            return None
        if not self._authorizer.is_allowed(message.author_id, message.guild_id, message.channel_id):
            return None
        if not self._bindings.mark_message_seen(message.message_id):
            return None
        key = DiscordBindingKey(message.guild_id, message.channel_id, message.thread_id)
        return DiscordIngressEvent(message=message, kind=classify_message(message.content), binding_key=key)


__all__ = [
    "DiscordIngressAdapter",
    "DiscordIngressEvent",
    "DiscordMessage",
    "DiscordMessageKind",
    "DiscordScope",
    "classify_message",
]
