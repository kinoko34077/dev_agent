"""Pure Discord ingress, message classification, and repository scope checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ..coordination.protocol_helpers import validate_relative_path, validate_text
from ..security.protected_paths import PathProtectionClass, classify_path
from .auth import DiscordAuthorizer
from .binding import DiscordBindingKey, InMemoryDiscordBindingStore, SQLiteDiscordBindingStore
from .history import DiscordHistoryMessage
from .intent import IntentKind, IntentProposal, resolve_plain_text
from .wait import parse_user_delay


class DiscordMessageKind(str, Enum):
    CHAT = "CHAT"
    NEW_REQUEST = "NEW_REQUEST"
    READ_QUERY = "READ_QUERY"
    NOTE = "NOTE"
    PARALLEL = "PARALLEL"
    INTERRUPT = "INTERRUPT"
    CANCEL = "CANCEL"
    WAIT = "WAIT"


def classify_message(content: str, *, active_run: bool = False) -> DiscordMessageKind:
    """Classify one message, keeping explicit commands authoritative.

    Plain language is intentionally context-sensitive: a bound non-terminal
    run receives ordinary follow-up text as a NOTE, while an unbound or
    terminal binding starts a new Operation request.  Explicit commands are
    resolved before that default is applied.
    """

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
    if lowered == "/new" or lowered.startswith("/new "):
        return DiscordMessageKind.NEW_REQUEST
    # Explicit finite waits are a Host-owned fast path.  They must not depend
    # on an advisory intent resolver being present or available.
    if parse_user_delay(normalized) is not None:
        return DiscordMessageKind.WAIT
    if active_run:
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
    reference_message_id: str | None = None

    def __post_init__(self) -> None:
        key = DiscordBindingKey(self.guild_id, self.channel_id, self.thread_id)
        object.__setattr__(self, "guild_id", key.guild_id)
        object.__setattr__(self, "channel_id", key.channel_id)
        object.__setattr__(self, "thread_id", key.thread_id)
        for name, value in (("message_id", self.message_id), ("author_id", self.author_id)):
            if not isinstance(value, str) or not value.strip().isdecimal() or len(value.strip()) > 32:
                raise ValueError(f"{name} must be a bounded Discord numeric ID")
            object.__setattr__(self, name, value.strip())
        if self.reference_message_id is not None:
            if (
                not isinstance(self.reference_message_id, str)
                or not self.reference_message_id.strip().isdecimal()
                or len(self.reference_message_id.strip()) > 32
            ):
                raise ValueError("reference_message_id must be a bounded Discord numeric ID")
            object.__setattr__(self, "reference_message_id", self.reference_message_id.strip())
        if not isinstance(self.content, str) or not self.content.strip() or len(self.content) > 4_000:
            raise ValueError("content must be bounded non-empty text")
        if not isinstance(self.author_is_bot, bool):
            raise ValueError("author_is_bot must be boolean")


@dataclass(frozen=True)
class DiscordIngressEvent:
    message: DiscordMessage
    kind: DiscordMessageKind
    binding_key: DiscordBindingKey
    history_context: tuple[DiscordHistoryMessage, ...] = ()
    intent_proposal: IntentProposal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.history_context, tuple):
            object.__setattr__(self, "history_context", tuple(self.history_context))
        if not all(isinstance(item, DiscordHistoryMessage) for item in self.history_context):
            raise TypeError("history_context must contain DiscordHistoryMessage values")
        if self.intent_proposal is not None and not isinstance(self.intent_proposal, IntentProposal):
            raise TypeError("intent_proposal must be an IntentProposal or None")


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

    def __init__(
        self,
        *,
        authorizer: DiscordAuthorizer,
        bindings: InMemoryDiscordBindingStore | SQLiteDiscordBindingStore,
        binding_is_active: Callable[[DiscordBindingKey], bool] | None = None,
        intent_resolver: Callable[..., IntentProposal] | None = resolve_plain_text,
    ) -> None:
        if not isinstance(authorizer, DiscordAuthorizer):
            raise TypeError("authorizer must be DiscordAuthorizer")
        if not isinstance(bindings, (InMemoryDiscordBindingStore, SQLiteDiscordBindingStore)):
            raise TypeError("bindings must implement the Discord binding contract")
        if binding_is_active is not None and not callable(binding_is_active):
            raise TypeError("binding_is_active must be callable")
        self._authorizer = authorizer
        self._bindings = bindings
        self._binding_is_active = binding_is_active
        self._intent_resolver = intent_resolver

    def accept(
        self,
        message: DiscordMessage,
        *,
        history_context: tuple[DiscordHistoryMessage, ...] = (),
        current_status: str | None = None,
    ) -> DiscordIngressEvent | None:
        if not isinstance(message, DiscordMessage) or message.author_is_bot:
            return None
        if not self._authorizer.is_allowed(message.author_id, message.guild_id, message.channel_id):
            return None
        key = DiscordBindingKey(message.guild_id, message.channel_id, message.thread_id)
        has_binding = self._bindings.lookup(key) is not None
        active = bool(self._binding_is_active(key)) if self._binding_is_active and has_binding else False
        kind = classify_message(message.content, active_run=active)
        proposal = None
        delay_seconds = parse_user_delay(message.content) if kind is DiscordMessageKind.WAIT else None
        if delay_seconds is not None:
            proposal = IntentProposal(IntentKind.WAIT, message.content, delay_seconds)
        if self._intent_resolver is not None and kind in {
            DiscordMessageKind.NEW_REQUEST,
            DiscordMessageKind.NOTE,
        }:
            try:
                proposal = self._intent_resolver(
                    message.content,
                    active_run=active,
                    has_binding=has_binding,
                    history_context=tuple(item.to_dict(include_metadata=True) for item in history_context),
                    reply_to_message_id=message.reference_message_id,
                    current_status=current_status,
                )
            except (TypeError, ValueError):
                # The proposal layer is advisory.  Existing deterministic
                # command/context routing remains the fail-closed fallback.
                proposal = None
            if proposal is not None:
                kind = {
                    IntentKind.CHAT: DiscordMessageKind.CHAT,
                    IntentKind.NEW_REQUEST: DiscordMessageKind.NEW_REQUEST,
                    IntentKind.FOLLOW_UP: DiscordMessageKind.NOTE if active else DiscordMessageKind.NEW_REQUEST,
                    IntentKind.READ_QUERY: DiscordMessageKind.READ_QUERY,
                    IntentKind.WAIT: DiscordMessageKind.WAIT,
                }[proposal.kind]
        if not self._bindings.mark_message_seen(message.message_id, binding_key=key, kind=kind.value):
            return None
        return DiscordIngressEvent(
            message=message,
            kind=kind,
            binding_key=key,
            history_context=history_context,
            intent_proposal=proposal,
        )


__all__ = [
    "DiscordIngressAdapter",
    "DiscordIngressEvent",
    "DiscordMessage",
    "DiscordMessageKind",
    "DiscordScope",
    "classify_message",
]
