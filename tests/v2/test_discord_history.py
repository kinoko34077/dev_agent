from __future__ import annotations

import asyncio

from src.dev_agent.discord.auth import DiscordAuthorizer
from src.dev_agent.discord.history import DiscordHistoryMessage, collect_discord_history


class _Author:
    def __init__(self, user_id: int, *, bot: bool = False) -> None:
        self.id = user_id
        self.bot = bot


class _HistoryMessage:
    def __init__(
        self,
        message_id: int,
        author_id: int,
        content: str,
        *,
        channel_id: int = 20,
        bot: bool = False,
        webhook_id: int | None = None,
    ) -> None:
        self.id = message_id
        self.author = _Author(author_id, bot=bot)
        self.content = content
        self.channel = type("MessageChannel", (), {"id": channel_id})()
        self.webhook_id = webhook_id


class _HistoryChannel:
    def __init__(self, messages: list[_HistoryMessage], *, channel_id: int = 20) -> None:
        self.id = channel_id
        self._messages = messages
        self.calls: list[dict[str, object]] = []

    def history(self, **kwargs):
        self.calls.append(kwargs)

        async def _iterate():
            for message in self._messages:
                yield message

        return _iterate()


def _authorizer() -> DiscordAuthorizer:
    return DiscordAuthorizer(
        allowed_user_ids={"42"},
        allowed_guild_ids={"10"},
        allowed_channel_ids={"20"},
    )


def test_history_excludes_current_message_and_keeps_authorized_human_and_dev_agent_bot():
    current = _HistoryMessage(999, 42, "current message")
    channel = _HistoryChannel(
        [
            _HistoryMessage(1, 42, "先に確認して"),
            _HistoryMessage(2, 99, "確認を開始します", bot=True),
            _HistoryMessage(3, 77, "unauthorized", bot=False),
            _HistoryMessage(4, 88, "unrelated bot", bot=True),
            current,
        ]
    )

    result = asyncio.run(
        collect_discord_history(
            channel,
            current_message_id="999",
            current_message=current,
            authorizer=_authorizer(),
            guild_id="10",
            channel_id="20",
            bot_user_id="99",
        )
    )

    assert result == (
        DiscordHistoryMessage(role="human", content="先に確認して"),
        DiscordHistoryMessage(role="assistant", content="確認を開始します"),
    )
    assert channel.calls[0]["limit"] == 20
    assert channel.calls[0]["before"] is current
    assert channel.calls[0]["oldest_first"] is True


def test_history_is_bounded_by_message_count_and_total_characters():
    channel = _HistoryChannel([_HistoryMessage(index, 42, f"message-{index}") for index in range(1, 30)])

    result = asyncio.run(
        collect_discord_history(
            channel,
            current_message_id="999",
            authorizer=_authorizer(),
            guild_id="10",
            channel_id="20",
            bot_user_id="99",
            limit=20,
            max_chars=25,
        )
    )

    assert len(result) <= 20
    assert sum(len(item.content) for item in result) <= 25
    assert [item.content for item in result] == ["message-1", "message-2", "message"]


def test_history_is_chronological_and_thread_scoped():
    channel = _HistoryChannel([], channel_id=20)
    messages = [
        _HistoryMessage(1, 42, "thread first", channel_id=30),
        _HistoryMessage(2, 42, "parent channel", channel_id=20),
        _HistoryMessage(3, 99, "thread reply", channel_id=30, bot=True),
        _HistoryMessage(4, 42, "other thread", channel_id=31),
    ]
    channel._messages = messages

    result = asyncio.run(
        collect_discord_history(
            channel,
            current_message_id="999",
            authorizer=_authorizer(),
            guild_id="10",
            channel_id="20",
            thread_id="30",
            bot_user_id="99",
        )
    )

    assert [item.content for item in result] == ["thread first", "thread reply"]


def test_history_redacts_secrets_and_returns_context_only():
    channel = _HistoryChannel([_HistoryMessage(1, 42, "api_key=do-not-store")])

    result = asyncio.run(
        collect_discord_history(
            channel,
            current_message_id="999",
            authorizer=_authorizer(),
            guild_id="10",
            channel_id="20",
            bot_user_id="99",
        )
    )

    assert result[0].to_dict() == {"role": "human", "content": "[REDACTED]"}
    assert not hasattr(result[0], "message")
    assert not hasattr(result[0], "replay")
