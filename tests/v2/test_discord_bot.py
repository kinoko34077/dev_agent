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
