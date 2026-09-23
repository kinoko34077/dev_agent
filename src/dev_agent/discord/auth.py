"""Fail-closed Discord identity authorization for the thin UI boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


_MAX_SNOWFLAKE_CHARS = 32


def _snowflake(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a Discord numeric ID")
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_SNOWFLAKE_CHARS or not normalized.isdecimal():
        raise ValueError(f"{name} must be a bounded Discord numeric ID")
    return normalized


def _id_set(values: Iterable[str], name: str) -> frozenset[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a sequence of Discord IDs")
    return frozenset(_snowflake(value, name) for value in values)


@dataclass(frozen=True)
class DiscordAuthorizer:
    """Allow only configured Discord numeric identities and locations.

    An empty policy is intentionally fail-closed for operations.  Echo-only
    Gateway behavior can remain available before an operator configures an
    allowlist, but the adapter will not accept that message as work.
    """

    allowed_user_ids: frozenset[str] = frozenset()
    allowed_guild_ids: frozenset[str] = frozenset()
    allowed_channel_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_user_ids", _id_set(self.allowed_user_ids, "allowed_user_ids"))
        object.__setattr__(self, "allowed_guild_ids", _id_set(self.allowed_guild_ids, "allowed_guild_ids"))
        object.__setattr__(self, "allowed_channel_ids", _id_set(self.allowed_channel_ids, "allowed_channel_ids"))

    def is_allowed(self, user_id: str, guild_id: str, channel_id: str) -> bool:
        try:
            user_id = _snowflake(user_id, "user_id")
            guild_id = _snowflake(guild_id, "guild_id")
            channel_id = _snowflake(channel_id, "channel_id")
        except ValueError:
            return False
        if not self.allowed_user_ids:
            return False
        if user_id not in self.allowed_user_ids:
            return False
        if self.allowed_guild_ids and guild_id not in self.allowed_guild_ids:
            return False
        if self.allowed_channel_ids and channel_id not in self.allowed_channel_ids:
            return False
        return True

    def is_user_allowed(self, user_id: str) -> bool:
        """Check the configured Human identity without inventing a location."""
        try:
            normalized = _snowflake(user_id, "user_id")
        except ValueError:
            return False
        return bool(self.allowed_user_ids) and normalized in self.allowed_user_ids


__all__ = ["DiscordAuthorizer"]
