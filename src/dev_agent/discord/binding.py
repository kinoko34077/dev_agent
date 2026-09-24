"""Pointer-only Discord channel/root binding for the MVP boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any

from ..coordination.protocol_helpers import validate_identifier, validate_relative_path


_MAX_POINTERS = 2048
_MAX_MESSAGES = 4096
_MAX_ID_CHARS = 32
_MAX_SCOPE_FILES = 32


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


@dataclass(frozen=True)
class DiscordScopeState:
    """Small UI-owned scope pointer; never a copy of Task state."""

    directory_scope: str | None = None
    selected_files: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.directory_scope is not None:
            object.__setattr__(self, "directory_scope", validate_relative_path(self.directory_scope, "directory_scope"))
        if not isinstance(self.selected_files, tuple) or len(self.selected_files) > _MAX_SCOPE_FILES:
            raise ValueError("selected_files must be a bounded tuple")
        normalized = tuple(validate_relative_path(item, "selected_file") for item in self.selected_files)
        if len(set(normalized)) != len(normalized):
            raise ValueError("selected_files must be unique")
        object.__setattr__(self, "selected_files", normalized)


@dataclass(frozen=True)
class DiscordHistorySyncState:
    """Durable cursor metadata for Discord-to-log synchronization only."""

    binding_key: str
    latest_synced_message_id: str | None = None
    oldest_seeded_message_id: str | None = None
    seeded: bool = False
    last_sync_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.binding_key, str) or not self.binding_key.strip() or len(self.binding_key) > 256:
            raise ValueError("binding_key must be bounded non-empty text")
        for value, name in (
            (self.latest_synced_message_id, "latest_synced_message_id"),
            (self.oldest_seeded_message_id, "oldest_seeded_message_id"),
        ):
            if value is not None:
                _discord_id(value, name)
        if not isinstance(self.seeded, bool):
            raise ValueError("seeded must be boolean")
        if not isinstance(self.last_sync_at, str) or not self.last_sync_at.strip() or len(self.last_sync_at) > 128:
            raise ValueError("last_sync_at must be bounded non-empty text")


class InMemoryDiscordBindingStore:
    """MVP pointer/idempotency store; never a Task or Conversation SSOT."""

    def __init__(self, *, max_bindings: int = _MAX_POINTERS, max_messages: int = _MAX_MESSAGES) -> None:
        if max_bindings < 1 or max_messages < 1:
            raise ValueError("binding limits must be positive")
        self._max_bindings = max_bindings
        self._max_messages = max_messages
        self._bindings: dict[DiscordBindingKey, DiscordBinding] = {}
        self._messages: dict[str, None] = {}
        self._scopes: dict[DiscordBindingKey, DiscordScopeState] = {}
        self._history_sync: dict[DiscordBindingKey, DiscordHistorySyncState] = {}
        self._deliveries: dict[tuple[str, str], None] = {}

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

    def list_bindings(self) -> list[DiscordBinding]:
        return list(self._bindings.values())

    def find_for_task(self, task_id: str) -> DiscordBinding | None:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        for binding in self._bindings.values():
            if task_id in {binding.root_id, binding.run_id}:
                return binding
        return None

    def mark_message_seen(self, message_id: str, *, binding_key: DiscordBindingKey | None = None, kind: str = "", received_at: str | None = None) -> bool:
        message_id = _discord_id(message_id, "message_id")
        if message_id in self._messages:
            return False
        self._messages[message_id] = None
        while len(self._messages) > self._max_messages:
            self._messages.pop(next(iter(self._messages)))
        return True

    def save_scope(self, key: DiscordBindingKey, *, directory_scope: str | None, selected_files: tuple[str, ...] = ()) -> DiscordScopeState:
        if not isinstance(key, DiscordBindingKey):
            raise ValueError("key must be DiscordBindingKey")
        scope = DiscordScopeState(directory_scope=directory_scope, selected_files=selected_files)
        self._scopes[key] = scope
        return scope

    def get_scope(self, key: DiscordBindingKey) -> DiscordScopeState | None:
        if not isinstance(key, DiscordBindingKey):
            raise ValueError("key must be DiscordBindingKey")
        return self._scopes.get(key)

    def save_history_sync(
        self,
        key: DiscordBindingKey,
        *,
        latest_synced_message_id: str | None,
        oldest_seeded_message_id: str | None,
        seeded: bool,
        last_sync_at: str | None = None,
    ) -> DiscordHistorySyncState:
        state = DiscordHistorySyncState(
            binding_key="|".join((key.guild_id, key.channel_id, key.thread_id)),
            latest_synced_message_id=latest_synced_message_id,
            oldest_seeded_message_id=oldest_seeded_message_id,
            seeded=seeded,
            last_sync_at=last_sync_at or _now(),
        )
        self._history_sync[key] = state
        return state

    def get_history_sync(self, key: DiscordBindingKey) -> DiscordHistorySyncState | None:
        if not isinstance(key, DiscordBindingKey):
            raise ValueError("key must be DiscordBindingKey")
        return self._history_sync.get(key)

    def record_delivery(self, request_id: str, discord_message_id: str, *, delivered_at: str | None = None) -> bool:
        marker = (validate_identifier(request_id, "request_id"), _discord_id(discord_message_id, "discord_message_id"))
        if marker in self._deliveries:
            return False
        self._deliveries[marker] = None
        return True

    def has_any_delivery(self, request_id: str) -> bool:
        request_id = validate_identifier(request_id, "request_id")
        return any(item[0] == request_id for item in self._deliveries)

    def request_id_for_delivery(self, discord_message_id: str) -> str | None:
        discord_message_id = _discord_id(discord_message_id, "discord_message_id")
        for request_id, message_id in self._deliveries:
            if message_id == discord_message_id:
                return request_id
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteDiscordBindingStore:
    """Durable Discord pointers and delivery/idempotency metadata on Core StateStore."""

    def __init__(self, store: Any) -> None:
        required = (
            "save_discord_binding",
            "get_discord_binding",
            "list_discord_bindings",
            "mark_discord_message_seen",
            "record_discord_delivery",
            "has_discord_delivery",
            "has_any_discord_delivery",
            "discord_request_id_for_message",
            "save_discord_scope",
            "get_discord_scope",
            "save_discord_history_sync",
            "get_discord_history_sync",
        )
        if any(not callable(getattr(store, name, None)) for name in required):
            raise TypeError("store does not implement the Discord persistence contract")
        self._store = store

    @staticmethod
    def _key(key: DiscordBindingKey) -> str:
        if not isinstance(key, DiscordBindingKey):
            raise ValueError("key must be DiscordBindingKey")
        return "|".join((key.guild_id, key.channel_id, key.thread_id))

    def bind(self, key: DiscordBindingKey, *, root_id: str, run_id: str, updated_at: str | None = None) -> DiscordBinding:
        binding = DiscordBinding(key=key, root_id=root_id, run_id=run_id)
        self._store.save_discord_binding(
            binding_key=self._key(binding.key),
            guild_id=binding.key.guild_id,
            channel_id=binding.key.channel_id,
            thread_id=binding.key.thread_id,
            root_id=binding.root_id,
            run_id=binding.run_id,
            updated_at=updated_at or _now(),
        )
        return binding

    def lookup(self, key: DiscordBindingKey) -> DiscordBinding | None:
        row = self._store.get_discord_binding(self._key(key))
        if row is None:
            return None
        return DiscordBinding(
            key=DiscordBindingKey(row["guild_id"], row["channel_id"], row["thread_id"]),
            root_id=row["root_id"],
            run_id=row["run_id"],
        )

    def list_bindings(self) -> list[DiscordBinding]:
        return [
            DiscordBinding(
                key=DiscordBindingKey(row["guild_id"], row["channel_id"], row["thread_id"]),
                root_id=row["root_id"],
                run_id=row["run_id"],
            )
            for row in self._store.list_discord_bindings()
        ]

    def find_for_task(self, task_id: str) -> DiscordBinding | None:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        for binding in self.list_bindings():
            if task_id in {binding.root_id, binding.run_id}:
                return binding
        return None

    def mark_message_seen(self, message_id: str, *, binding_key: DiscordBindingKey | None = None, kind: str = "UNKNOWN", received_at: str | None = None) -> bool:
        message_id = _discord_id(message_id, "message_id")
        if not isinstance(kind, str) or not kind.strip() or len(kind) > 64:
            raise ValueError("kind must be bounded text")
        key = self._key(binding_key) if binding_key is not None else "||"
        return self._store.mark_discord_message_seen(
            message_id=message_id,
            binding_key=key,
            kind=kind.strip(),
            received_at=received_at or _now(),
        )

    def record_delivery(self, request_id: str, discord_message_id: str, *, delivered_at: str | None = None) -> bool:
        request_id = validate_identifier(request_id, "request_id")
        discord_message_id = _discord_id(discord_message_id, "discord_message_id")
        return self._store.record_discord_delivery(
            request_id=request_id,
            discord_message_id=discord_message_id,
            delivered_at=delivered_at or _now(),
        )

    def has_delivery(self, request_id: str, discord_message_id: str) -> bool:
        return self._store.has_discord_delivery(
            request_id=validate_identifier(request_id, "request_id"),
            discord_message_id=_discord_id(discord_message_id, "discord_message_id"),
        )

    def has_any_delivery(self, request_id: str) -> bool:
        return self._store.has_any_discord_delivery(
            request_id=validate_identifier(request_id, "request_id"),
        )

    def request_id_for_delivery(self, discord_message_id: str) -> str | None:
        return self._store.discord_request_id_for_message(
            _discord_id(discord_message_id, "discord_message_id"),
        )

    def save_scope(
        self,
        key: DiscordBindingKey,
        *,
        directory_scope: str | None,
        selected_files: tuple[str, ...] = (),
        updated_at: str | None = None,
    ) -> DiscordScopeState:
        scope = DiscordScopeState(directory_scope=directory_scope, selected_files=selected_files)
        self._store.save_discord_scope(
            binding_key=self._key(key),
            directory_scope=scope.directory_scope,
            selected_files_payload=json.dumps(list(scope.selected_files), ensure_ascii=False, separators=(",", ":")),
            updated_at=updated_at or _now(),
        )
        return scope

    def get_scope(self, key: DiscordBindingKey) -> DiscordScopeState | None:
        row = self._store.get_discord_scope(self._key(key))
        if row is None:
            return None
        try:
            selected_files = tuple(json.loads(row["selected_files_payload"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("stored Discord scope is malformed") from exc
        return DiscordScopeState(directory_scope=row.get("directory_scope"), selected_files=selected_files)

    def save_history_sync(
        self,
        key: DiscordBindingKey,
        *,
        latest_synced_message_id: str | None,
        oldest_seeded_message_id: str | None,
        seeded: bool,
        last_sync_at: str | None = None,
    ) -> DiscordHistorySyncState:
        state = DiscordHistorySyncState(
            binding_key=self._key(key),
            latest_synced_message_id=latest_synced_message_id,
            oldest_seeded_message_id=oldest_seeded_message_id,
            seeded=seeded,
            last_sync_at=last_sync_at or _now(),
        )
        self._store.save_discord_history_sync(
            binding_key=state.binding_key,
            latest_synced_message_id=state.latest_synced_message_id,
            oldest_seeded_message_id=state.oldest_seeded_message_id,
            seeded=state.seeded,
            last_sync_at=state.last_sync_at,
        )
        return state

    def get_history_sync(self, key: DiscordBindingKey) -> DiscordHistorySyncState | None:
        row = self._store.get_discord_history_sync(self._key(key))
        if row is None:
            return None
        return DiscordHistorySyncState(
            binding_key=row["binding_key"],
            latest_synced_message_id=row.get("latest_synced_message_id"),
            oldest_seeded_message_id=row.get("oldest_seeded_message_id"),
            seeded=bool(row.get("seeded")),
            last_sync_at=row["last_sync_at"],
        )


__all__ = ["DiscordBinding", "DiscordBindingKey", "DiscordHistorySyncState", "DiscordScopeState", "InMemoryDiscordBindingStore", "SQLiteDiscordBindingStore"]
