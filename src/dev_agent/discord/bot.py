"""Optional discord.py Gateway entrypoint for the thin Human UI adapter."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import inspect
import os
from pathlib import Path
import re
from typing import Any, Mapping

from .adapter import DiscordIngressAdapter, DiscordMessage, DiscordScope
from .approval import DiscordApprovalAdapter
from .auth import DiscordAuthorizer
from .binding import InMemoryDiscordBindingStore, SQLiteDiscordBindingStore
from .renderer import render_echo, render_progress


class DiscordConfigurationError(ValueError):
    """Raised when required local Discord configuration is absent or invalid."""


class DiscordDependencyError(RuntimeError):
    """Raised when the optional discord.py package is not installed."""


_MAX_ENV_BYTES = 64 * 1024
_TOKEN_MAX_CHARS = 512
_PUBLIC_KEY = re.compile(r"^[0-9a-fA-F]{64}$")


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if not path.is_file() or path.stat().st_size > _MAX_ENV_BYTES:
        raise DiscordConfigurationError("Discord env file is missing, not a file, or too large")
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", name.strip()):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[name.strip()] = value
    return values


def _required(values: Mapping[str, str], name: str, *, maximum: int) -> str:
    value = values.get(name, "").strip()
    if not value or len(value) > maximum or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise DiscordConfigurationError(f"{name} is required and bounded")
    return value


def _numeric_id(value: str, name: str) -> str:
    if not value.isdecimal() or len(value) > 32:
        raise DiscordConfigurationError(f"{name} must be a Discord numeric ID")
    return value


def _optional_ids(values: Mapping[str, str], name: str) -> frozenset[str]:
    raw = values.get(name, "").strip()
    if not raw:
        return frozenset()
    result = set()
    for item in raw.split(","):
        result.add(_numeric_id(item.strip(), name))
    return frozenset(result)


@dataclass(frozen=True)
class DiscordBotConfig:
    application_id: str
    public_key: str
    bot_token: str = field(repr=False)
    allowed_user_ids: frozenset[str] = frozenset()
    allowed_guild_ids: frozenset[str] = frozenset()
    allowed_channel_ids: frozenset[str] = frozenset()

    @classmethod
    def from_environment(
        cls,
        *,
        env_path: str | Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "DiscordBotConfig":
        values = _parse_env_file(Path(env_path)) if env_path is not None else {}
        values.update(dict(os.environ if environ is None else environ))
        application_id = _numeric_id(_required(values, "DISCORD_APPLICATION_ID", maximum=32), "DISCORD_APPLICATION_ID")
        public_key = _required(values, "DISCORD_PUBLIC_KEY", maximum=64)
        if not _PUBLIC_KEY.fullmatch(public_key):
            raise DiscordConfigurationError("DISCORD_PUBLIC_KEY must be 64 hexadecimal characters")
        bot_token = _required(values, "DISCORD_BOT_TOKEN", maximum=_TOKEN_MAX_CHARS)
        return cls(
            application_id=application_id,
            public_key=public_key,
            bot_token=bot_token,
            allowed_user_ids=_optional_ids(values, "DISCORD_ALLOWED_USER_ID"),
            allowed_guild_ids=_optional_ids(values, "DISCORD_ALLOWED_GUILD_ID"),
            allowed_channel_ids=_optional_ids(values, "DISCORD_ALLOWED_CHANNEL_ID"),
        )

    def public_summary(self) -> dict[str, object]:
        return {
            "application_id": self.application_id,
            "public_key": "<present>",
            "bot_token": "<redacted>",
            "allowed_user_count": len(self.allowed_user_ids),
            "allowed_guild_count": len(self.allowed_guild_ids),
            "allowed_channel_count": len(self.allowed_channel_ids),
        }


def _discord_modules():
    try:
        import discord
        from discord.ext import commands
    except ImportError as exc:
        raise DiscordDependencyError("discord.py is required; install requirements-discord.txt") from exc
    return discord, commands


def _message_from_discord(message: Any) -> DiscordMessage:
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    thread = getattr(channel, "id", None) if channel is not None and channel.__class__.__name__ == "Thread" else None
    return DiscordMessage(
        message_id=str(message.id),
        author_id=str(message.author.id),
        guild_id=str(getattr(guild, "id", 0)),
        channel_id=str(getattr(channel, "id", 0)),
        thread_id="" if thread is None else str(thread),
        content=str(message.content),
        author_is_bot=bool(getattr(message.author, "bot", False)),
    )


def build_approval_view(*, approval_id: str, authorizer: DiscordAuthorizer, submit) -> Any:
    """Build a proposal-only Discord button view around Core approval input."""

    discord, _commands = _discord_modules()
    boundary = DiscordApprovalAdapter(authorizer=authorizer, submit=submit)

    class ApprovalView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=None)

        async def _handle(self, interaction: Any, approved: bool) -> None:
            guild = getattr(interaction, "guild", None)
            channel = getattr(interaction, "channel", None)
            try:
                result = boundary.submit(
                    approval_id,
                    author_id=str(interaction.user.id),
                    approved=approved,
                    guild_id=str(getattr(guild, "id", "")),
                    channel_id=str(getattr(channel, "id", "")),
                )
                if inspect.isawaitable(result):
                    await result
            except (PermissionError, ValueError, KeyError):
                await interaction.response.send_message("この承認操作は受け付けられません。", ephemeral=True)
                return
            await interaction.response.send_message("既存のApproval Authorityへ結果を送信しました。", ephemeral=True)

        @discord.ui.button(label="承認", style=discord.ButtonStyle.success)
        async def approve(self, interaction: Any, _button: Any) -> None:
            await self._handle(interaction, True)

        @discord.ui.button(label="拒否", style=discord.ButtonStyle.danger)
        async def reject(self, interaction: Any, _button: Any) -> None:
            await self._handle(interaction, False)

    return ApprovalView()


def build_bot(
    config: DiscordBotConfig,
    *,
    workspace: str | Path,
    bindings: InMemoryDiscordBindingStore | SQLiteDiscordBindingStore | None = None,
    on_event=None,
) -> Any:
    """Build the Gateway bot; no Discord connection starts until ``run``."""

    if not isinstance(config, DiscordBotConfig):
        raise TypeError("config must be DiscordBotConfig")
    discord, commands = _discord_modules()
    authorizer = DiscordAuthorizer(
        allowed_user_ids=config.allowed_user_ids,
        allowed_guild_ids=config.allowed_guild_ids,
        allowed_channel_ids=config.allowed_channel_ids,
    )
    bindings = bindings or InMemoryDiscordBindingStore()
    if not isinstance(bindings, (InMemoryDiscordBindingStore, SQLiteDiscordBindingStore)):
        raise TypeError("bindings must implement the Discord binding contract")
    ingress = DiscordIngressAdapter(authorizer=authorizer, bindings=bindings)
    scope = DiscordScope(Path(workspace))
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
    sync_state = {"done": False}

    @bot.event
    async def on_ready():
        if not sync_state["done"]:
            await bot.tree.sync()
            sync_state["done"] = True
        print("Discord Human UI bot ready")

    @bot.event
    async def on_message(message):
        if getattr(message.author, "bot", False):
            return
        try:
            event = ingress.accept(_message_from_discord(message))
        except ValueError:
            # Discord itself supplied malformed/unusable metadata; do not echo
            # or route it into the Core boundary.
            return
        if event is not None and on_event is not None:
            result = on_event(event)
            if inspect.isawaitable(result):
                await result
        await message.channel.send(render_echo(message.content))
        await bot.process_commands(message)

    @bot.tree.command(name="dir", description="作業対象ディレクトリを指定します")
    async def directory_command(interaction, path: str):
        if not authorizer.is_allowed(str(interaction.user.id), str(getattr(interaction.guild, "id", 0)), str(interaction.channel.id)):
            await interaction.response.send_message("この操作は許可されていません。", ephemeral=True)
            return
        try:
            resolved = scope.resolve_directory(path)
        except ValueError:
            await interaction.response.send_message("指定ディレクトリは許可されていません。", ephemeral=True)
            return
        await interaction.response.send_message(f"参照ディレクトリ: `{resolved}`", ephemeral=True)

    @bot.tree.command(name="file", description="参照ファイルを指定します")
    async def file_command(interaction, path: str):
        if not authorizer.is_allowed(str(interaction.user.id), str(getattr(interaction.guild, "id", 0)), str(interaction.channel.id)):
            await interaction.response.send_message("この操作は許可されていません。", ephemeral=True)
            return
        try:
            resolved = scope.resolve_file(path)
        except ValueError:
            await interaction.response.send_message("指定ファイルは許可されていません。", ephemeral=True)
            return
        await interaction.response.send_message(f"参照ファイル: `{resolved}`", ephemeral=True)

    bot._dev_agent_discord_ingress = ingress
    bot._dev_agent_discord_scope = scope
    bot._dev_agent_discord_bindings = bindings
    bot._dev_agent_discord_on_event = on_event
    return bot


def run_from_environment(*, env_path: str | Path | None = None, workspace: str | Path | None = None) -> None:
    root = Path(__file__).resolve().parents[3]
    config = DiscordBotConfig.from_environment(env_path=env_path or root / ".env")
    bot = build_bot(config, workspace=workspace or root)
    bot.run(config.bot_token)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description="Run the optional thin Discord Human UI adapter")
    parser.add_argument("--env-file", default=str(root / ".env"))
    parser.add_argument("--workspace", default=str(root))
    args = parser.parse_args(argv)
    run_from_environment(env_path=args.env_file, workspace=args.workspace)
    return 0


__all__ = [
    "DiscordBotConfig",
    "DiscordConfigurationError",
    "DiscordDependencyError",
    "build_bot",
    "build_approval_view",
    "main",
    "run_from_environment",
]
