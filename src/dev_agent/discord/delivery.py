"""Single bounded send boundary for Discord human-facing projections."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import math
from typing import Any


SleepCallback = Callable[[float], Awaitable[Any]]


def _channel_key(channel: Any) -> str:
    channel_id = getattr(channel, "id", None)
    if isinstance(channel_id, bool) or not isinstance(channel_id, (int, str)):
        raise ValueError("Discord channel must expose a numeric id")
    normalized = str(channel_id).strip()
    if not normalized.isdecimal() or len(normalized) > 32:
        raise ValueError("Discord channel must expose a bounded numeric id")
    parent_id = getattr(channel, "parent_id", None)
    parent = "" if parent_id is None else str(parent_id).strip()
    if parent and (not parent.isdecimal() or len(parent) > 32):
        raise ValueError("Discord channel parent must be a bounded numeric id")
    return f"{parent}|{normalized}"


class DiscordHumanFacingSender:
    """Serialize only Discord UI sends; it owns no Task or retry state."""

    def __init__(
        self,
        *,
        typing_delay_seconds: float = 2.0,
        sleep: SleepCallback = asyncio.sleep,
    ) -> None:
        if isinstance(typing_delay_seconds, bool) or not math.isfinite(typing_delay_seconds):
            raise ValueError("typing_delay_seconds must be finite")
        if typing_delay_seconds < 0:
            raise ValueError("typing_delay_seconds must not be negative")
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        self.typing_delay_seconds = float(typing_delay_seconds)
        self._sleep = sleep
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, channel: Any) -> asyncio.Lock:
        key = _channel_key(channel)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def send(self, channel: Any, content: str, *, view: Any | None = None) -> Any:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Discord content must be non-empty text")
        lock = self._lock_for(channel)
        async with lock:
            async with channel.typing():
                await self._sleep(self.typing_delay_seconds)
                if view is None:
                    return await channel.send(content)
                return await channel.send(content, view=view)


__all__ = ["DiscordHumanFacingSender"]
