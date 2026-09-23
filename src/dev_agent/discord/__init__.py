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
from .binding import DiscordBinding, DiscordBindingKey, DiscordScopeState, InMemoryDiscordBindingStore, SQLiteDiscordBindingStore
from .human import DiscordHumanAdapter
from .approval import DiscordApprovalAdapter
from .core import DiscordCoreAdapter
from .composition import DiscordRuntimeComposition
from .outbound import DiscordOutboundPublisher

__all__ = [
    "DiscordAuthorizer",
    "DiscordBinding",
    "DiscordBindingKey",
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
    "classify_message",
]
