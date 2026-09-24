"""Optional, thin Discord Human UI boundary.

The package deliberately contains no Task execution, scheduling, retry, or
approval authority.  Those remain owned by the existing dev_agent Core.
"""

from .adapter import (
    DiscordIngressAdapter,
    DiscordMessage,
    DiscordMessageKind,
    DiscordScope,
    DiscordIngressEvent,
    classify_message,
)
from .auth import DiscordAuthorizer
from .binding import DiscordBinding, DiscordBindingKey, DiscordHistorySyncState, DiscordScopeState, InMemoryDiscordBindingStore, SQLiteDiscordBindingStore
from .human import DiscordHumanAdapter
from .approval import DiscordApprovalAdapter
from .core import DiscordCoreAdapter
from .composition import DiscordRuntimeComposition
from .outbound import DiscordOutboundPublisher
from .history import DiscordHistoryMessage, collect_discord_history
from .context import sync_discord_history

__all__ = [
    "DiscordAuthorizer",
    "DiscordBinding",
    "DiscordBindingKey",
    "DiscordHistorySyncState",
    "DiscordScopeState",
    "DiscordIngressAdapter",
    "DiscordIngressEvent",
    "DiscordMessage",
    "DiscordMessageKind",
    "DiscordScope",
    "InMemoryDiscordBindingStore",
    "SQLiteDiscordBindingStore",
    "DiscordHumanAdapter",
    "DiscordApprovalAdapter",
    "DiscordCoreAdapter",
    "DiscordRuntimeComposition",
    "DiscordOutboundPublisher",
    "DiscordHistoryMessage",
    "collect_discord_history",
    "sync_discord_history",
    "classify_message",
]
