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
from .binding import DiscordBinding, DiscordBindingKey, InMemoryDiscordBindingStore

__all__ = [
    "DiscordAuthorizer",
    "DiscordBinding",
    "DiscordBindingKey",
    "DiscordIngressAdapter",
    "DiscordIngressEvent",
    "DiscordMessage",
    "DiscordMessageKind",
    "DiscordScope",
    "InMemoryDiscordBindingStore",
    "classify_message",
]
