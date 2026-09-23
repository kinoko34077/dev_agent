import asyncio
from pathlib import Path

import pytest

from src.dev_agent.discord.bot import (
    DiscordConfigurationError,
    DiscordDependencyError,
    DiscordBotConfig,
    build_bot,
    build_approval_view,
)
from src.dev_agent.discord.auth import DiscordAuthorizer
from src.dev_agent.discord.renderer import (
    render_echo,
    render_human_request,
    render_progress,
)
from src.dev_agent.human import HumanRequest


def _env_file(tmp_path: Path, *, token: str = "token-value") -> Path:
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            (
                "DISCORD_APPLICATION_ID=123456789012345678",
                "DISCORD_PUBLIC_KEY=" + "a" * 64,
                f"DISCORD_BOT_TOKEN={token}",
                "DISCORD_ALLOWED_USER_ID=42",
                "DISCORD_ALLOWED_GUILD_ID=10",
                "DISCORD_ALLOWED_CHANNEL_ID=20",
            )
        ),
        encoding="utf-8",
    )
    return path


def test_discord_config_reads_env_file_and_never_exposes_token_in_repr(tmp_path):
    config = DiscordBotConfig.from_environment(env_path=_env_file(tmp_path))

    assert config.application_id == "123456789012345678"
    assert config.allowed_user_ids == frozenset({"42"})
    assert "token-value" not in repr(config)
    assert config.public_summary()["bot_token"] == "<redacted>"


def test_discord_config_requires_token_without_logging_or_defaulting(tmp_path):
    path = _env_file(tmp_path, token="")

    with pytest.raises(DiscordConfigurationError, match="DISCORD_BOT_TOKEN"):
        DiscordBotConfig.from_environment(env_path=path)


def test_renderer_is_bounded_and_redacts_secret_shaped_text():
    echo = render_echo("send api_key=super-secret " + "x" * 4_000)
    progress = render_progress("host_verification", detail="authorization: Bearer secret-value")

    assert len(echo) <= 2_000
    assert len(progress) <= 2_000
    assert "super-secret" not in echo
    assert "Bearer secret-value" not in progress
    assert "Host Verification" in progress


def test_human_request_renderer_omits_unbounded_context():
    request = HumanRequest(
        root_id="root-1",
        task_id="task-1",
        attempt_id="attempt-1",
        reason="仕様が一意でない",
        question="A / B のどちらにしますか？",
        allowed_answers=("A", "B"),
        context={"secret": "must not render", "large": "x" * 10_000},
    )

    rendered = render_human_request(request)

    assert "A / B" in rendered
    assert "must not render" not in rendered
    assert len(rendered) <= 2_000


def test_build_bot_fails_bounded_when_optional_dependency_is_missing(tmp_path):
    config = DiscordBotConfig.from_environment(env_path=_env_file(tmp_path))
    try:
        import discord  # noqa: F401
    except ImportError:
        with pytest.raises(DiscordDependencyError, match="discord.py"):
            build_bot(config, workspace=tmp_path)


def test_approval_view_keeps_optional_dependency_lazy(monkeypatch):
    import src.dev_agent.discord.bot as discord_bot

    def missing_dependency():
        raise DiscordDependencyError("discord.py is required")

    monkeypatch.setattr(discord_bot, "_discord_modules", missing_dependency)
    with pytest.raises(DiscordDependencyError, match="discord.py"):
        build_approval_view(
            approval_id="approval-1",
            authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
            submit=lambda *_args: None,
        )


def test_unauthorized_message_is_silent_and_never_reaches_core(tmp_path):
    pytest.importorskip("discord")
    config = DiscordBotConfig.from_environment(env_path=_env_file(tmp_path))
    routed = []
    bot = build_bot(
        config,
        workspace=tmp_path,
        on_event=lambda event: routed.append(event),
    )
    handler = bot.on_message

    class _Author:
        id = 99
        bot = False

    class _Guild:
        id = 10

    class _Channel:
        id = 20

        def __init__(self):
            self.sent = []

        async def send(self, content):
            self.sent.append(content)

    class _Message:
        id = 901
        author = _Author()
        guild = _Guild()
        content = "このファイル確認して"

        def __init__(self):
            self.channel = _Channel()

    message = _Message()
    asyncio.run(handler(message))

    assert message.channel.sent == []
    assert routed == []


def test_authorized_read_query_renders_projection_instead_of_echo(tmp_path):
    pytest.importorskip("discord")
    config = DiscordBotConfig.from_environment(env_path=_env_file(tmp_path))
    bot = build_bot(
        config,
        workspace=tmp_path,
        on_event=lambda _event: {"current_action": "Host Verification"},
    )
    async def _ignore_commands(_message):
        return None

    bot.process_commands = _ignore_commands
    handler = bot.on_message

    class _Author:
        id = 42
        bot = False

    class _Guild:
        id = 10

    class _Channel:
        id = 20

        def __init__(self):
            self.sent = []

        async def send(self, content):
            self.sent.append(content)

    class _Message:
        id = 902
        author = _Author()
        guild = _Guild()
        content = "今何してる"

        def __init__(self):
            self.channel = _Channel()

    message = _Message()
    asyncio.run(handler(message))

    assert message.channel.sent == ["処理: Host Verification"]


def test_environment_runner_injects_durable_core_composition(tmp_path, monkeypatch):
    from src.dev_agent.discord import bot as discord_bot

    config_path = _env_file(tmp_path)
    captured = {}

    class _Bot:
        def run(self, token):
            captured["token"] = token

    def _build(config, **kwargs):
        captured["config"] = config
        captured["kwargs"] = kwargs
        return _Bot()

    monkeypatch.setattr(discord_bot, "build_bot", _build)
    discord_bot.run_from_environment(env_path=config_path, workspace=tmp_path)

    assert captured["token"] == "token-value"
    assert captured["kwargs"]["bindings"].__class__.__name__ == "SQLiteDiscordBindingStore"
    assert callable(captured["kwargs"]["on_event"])
    assert captured["kwargs"]["authorizer"].is_user_allowed("42") is True
