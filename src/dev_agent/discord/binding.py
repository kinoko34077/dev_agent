"""Pointer-only Discord channel/root binding for the MVP boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..coordination.protocol_helpers import validate_identifier


_MAX_POINTERS = 2048
_MAX_MESSAGES = 4096
_MAX_ID_CHARS = 32


def _discord_id(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a Discord numeric ID")
    normalized = value.strip()
    if allow_empty and not normalized:
        return ""
    if not normalized or len(normalized) > _MAX_ID_CHARS or not normalized.isdecimal():
        raise ValueError(f"{name} must be a bounded Discord numeric ID")
    return normalized


@dataclass(frozen=True)
class DiscordBindingKey:
    guild_id: str
    channel_id: str
    thread_id: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "guild_id", _discord_id(self.guild_id, "guild_id", allow_empty=True))
        object.__setattr__(self, "channel_id", _discord_id(self.channel_id, "channel_id"))
        object.__setattr__(self, "thread_id", _discord_id(self.thread_id, "thread_id", allow_empty=True))


@dataclass(frozen=True)
class DiscordBinding:
    key: DiscordBindingKey
    root_id: str
    run_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.key, DiscordBindingKey):
            raise ValueError("binding key must be DiscordBindingKey")
        object.__setattr__(self, "root_id", validate_identifier(self.root_id, "root_id"))
        object.__setattr__(self, "run_id", validate_identifier(self.run_id, "run_id"))


class InMemoryDiscordBindingStore:
    """MVP pointer/idempotency store; never a Task or Conversation SSOT."""

    def __init__(self, *, max_bindings: int = _MAX_POINTERS, max_messages: int = _MAX_MESSAGES) -> None:
        if max_bindings < 1 or max_messages < 1:
            raise ValueError("binding limits must be positive")
        self._max_bindings = max_bindings
        self._max_messages = max_messages
        self._bindings: dict[DiscordBindingKey, DiscordBinding] = {}
        self._messages: dict[str, None] = {}

    def bind(self, key: DiscordBindingKey, *, root_id: str, run_id: str) -> DiscordBinding:
        if not isinstance(key, DiscordBindingKey):
            raise ValueError("key must be DiscordBindingKey")
        binding = DiscordBinding(key=key, root_id=root_id, run_id=run_id)
        self._bindings[key] = binding
        while len(self._bindings) > self._max_bindings:
            self._bindings.pop(next(iter(self._bindings)))
        return binding

    def lookup(self, key: DiscordBindingKey) -> DiscordBinding | None:
        if not isinstance(key, DiscordBindingKey):
            raise ValueError("key must be DiscordBindingKey")
        return self._bindings.get(key)

    def mark_message_seen(self, message_id: str) -> bool:
        message_id = _discord_id(message_id, "message_id")
        if message_id in self._messages:
            return False
        self._messages[message_id] = None
        while len(self._messages) > self._max_messages:
            self._messages.pop(next(iter(self._messages)))
        return True


__all__ = ["DiscordBinding", "DiscordBindingKey", "InMemoryDiscordBindingStore"]
