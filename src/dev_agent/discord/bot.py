"""Optional discord.py Gateway entrypoint for the thin Human UI adapter."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
import hashlib
import inspect
import os
from pathlib import Path
import re
from typing import Any, Mapping

from .adapter import DiscordIngressAdapter, DiscordMessage, DiscordMessageKind, DiscordScope
from .approval import DiscordApprovalAdapter
from .auth import DiscordAuthorizer
from .binding import DiscordBindingKey, InMemoryDiscordBindingStore, SQLiteDiscordBindingStore
from .composition import DiscordRuntimeComposition
from .context import build_bounded_context, conversation_message_from_discord, sync_discord_history
from .conversation_archive import archive_eligible_messages
from .conversation_log import ConversationLog
from .delivery import DiscordHumanFacingSender
from .history import collect_discord_history
from .human import DiscordHumanAdapter
from .outbound import DiscordOutboundPublisher
from .renderer import render_chat_response, render_ingress_ack, render_progress, render_read_projection
from .intent import ProposalOnlyIntentResolver, resolve_plain_text
from ..human import HumanRequest
from ..operation import OperationConfig, OperationService


class DiscordConfigurationError(ValueError):
    """Raised when required local Discord configuration is absent or invalid."""


class DiscordDependencyError(RuntimeError):
    """Raised when the optional discord.py package is not installed."""


_MAX_ENV_BYTES = 64 * 1024
_TOKEN_MAX_CHARS = 512
_COMMAND_SYNC_TIMEOUT_SECONDS = 15.0
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


def _binding_key_from_discord(guild: Any, channel: Any) -> DiscordBindingKey:
    guild_id = str(getattr(guild, "id", 0))
    if channel is not None and channel.__class__.__name__ == "Thread":
        channel_id = str(getattr(channel, "parent_id", None) or getattr(channel, "id", 0))
        thread_id = str(getattr(channel, "id", 0))
    else:
        channel_id = str(getattr(channel, "id", 0))
        thread_id = ""
    return DiscordBindingKey(guild_id, channel_id, thread_id)


def _component_id(kind: str, identifier: str, suffix: str = "") -> str:
    """Build a stable, bounded component id without embedding UI content."""

    safe_identifier = re.sub(r"[^A-Za-z0-9_-]", "-", str(identifier).strip())
    if not safe_identifier:
        raise ValueError("component identifier must be non-empty")
    prefix = f"devagent:{kind}:"
    tail = f":{suffix}" if suffix else ""
    candidate = f"{prefix}{safe_identifier}{tail}"
    if len(candidate) <= 100:
        return candidate
    digest = hashlib.sha256(str(identifier).encode("utf-8", errors="replace")).hexdigest()[:32]
    return f"{prefix}{digest}{tail}"[:100]


def _message_from_discord(message: Any) -> DiscordMessage:
    guild = getattr(message, "guild", None)
    channel = getattr(message, "channel", None)
    binding_key = _binding_key_from_discord(guild, channel)
    reference = getattr(message, "reference", None)
    reference_id = getattr(reference, "message_id", None) if reference is not None else None
    return DiscordMessage(
        message_id=str(message.id),
        author_id=str(message.author.id),
        guild_id=binding_key.guild_id,
        channel_id=binding_key.channel_id,
        thread_id=binding_key.thread_id,
        content=str(message.content),
        author_is_bot=bool(getattr(message.author, "bot", False)),
        reference_message_id=None if reference_id is None else str(reference_id),
    )


def build_approval_view(*, approval_id: str, authorizer: DiscordAuthorizer, submit) -> Any:
    """Build a proposal-only Discord button view around Core approval input."""

    discord, _commands = _discord_modules()
    boundary = DiscordApprovalAdapter(authorizer=authorizer, submit=submit)

    class ApprovalView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=None)

            approve = discord.ui.Button(
                label="承認",
                style=discord.ButtonStyle.success,
                custom_id=_component_id("approval", approval_id, "yes"),
            )
            reject = discord.ui.Button(
                label="拒否",
                style=discord.ButtonStyle.danger,
                custom_id=_component_id("approval", approval_id, "no"),
            )

            async def _approve(interaction: Any) -> None:
                await self._handle(interaction, True)

            async def _reject(interaction: Any) -> None:
                await self._handle(interaction, False)

            approve.callback = _approve
            reject.callback = _reject
            self.add_item(approve)
            self.add_item(reject)

        async def _handle(self, interaction: Any, approved: bool) -> None:
            guild = getattr(interaction, "guild", None)
            channel = getattr(interaction, "channel", None)
            try:
                binding_key = _binding_key_from_discord(guild, channel)
                result = boundary.submit(
                    approval_id,
                    author_id=str(interaction.user.id),
                    approved=approved,
                    guild_id=binding_key.guild_id,
                    channel_id=binding_key.channel_id,
                )
                if inspect.isawaitable(result):
                    await result
            except (PermissionError, ValueError, KeyError):
                await interaction.response.send_message("この承認操作は受け付けられません。", ephemeral=True)
                return
            await interaction.response.send_message("既存のApproval Authorityへ結果を送信しました。", ephemeral=True)

    return ApprovalView()


def build_human_request_view(*, request: HumanRequest, authorizer: DiscordAuthorizer, submit) -> Any:
    """Build finite-answer buttons that delegate to HumanInteractionPort."""

    discord, _commands = _discord_modules()
    if not isinstance(request, HumanRequest):
        raise TypeError("request must be HumanRequest")
    if not callable(submit):
        raise TypeError("submit must be callable")

    class HumanRequestView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=None)
            answers = tuple(request.allowed_answers)

            async def _submit_selected(interaction: Any, selected: str) -> None:
                guild = getattr(interaction, "guild", None)
                channel = getattr(interaction, "channel", None)
                binding_key = _binding_key_from_discord(guild, channel)
                try:
                    result = submit(
                        request_id=request.request_id,
                        author_id=str(interaction.user.id),
                        response={"decision": selected},
                        decision=selected,
                        guild_id=binding_key.guild_id,
                        channel_id=binding_key.channel_id,
                    )
                    if inspect.isawaitable(result):
                        await result
                except (PermissionError, ValueError, KeyError):
                    await interaction.response.send_message("この回答は受け付けられません。", ephemeral=True)
                    return
                await interaction.response.send_message("回答を受け付けました。", ephemeral=True)

            if 1 <= len(answers) <= 5:
                for index, answer in enumerate(answers):
                    button = discord.ui.Button(
                        label=str(answer)[:80],
                        style=discord.ButtonStyle.primary,
                        custom_id=_component_id("human", request.request_id, str(index)),
                    )

                    async def _callback(interaction: Any, selected: str = answer) -> None:
                        await _submit_selected(interaction, selected)

                    button.callback = _callback
                    self.add_item(button)
            elif 6 <= len(answers) <= 25:
                select = discord.ui.Select(
                    custom_id=_component_id("human", request.request_id, "select"),
                    placeholder="選択してください",
                    min_values=1,
                    max_values=1,
                    options=[
                        discord.SelectOption(label=str(answer)[:100], value=str(index))
                        for index, answer in enumerate(answers)
                    ],
                )

                async def _select_callback(interaction: Any) -> None:
                    values = getattr(select, "values", ())
                    try:
                        selected = answers[int(values[0])]
                    except (IndexError, TypeError, ValueError):
                        await interaction.response.send_message("この回答は受け付けられません。", ephemeral=True)
                        return
                    await _submit_selected(interaction, selected)

                select.callback = _select_callback
                self.add_item(select)

    return HumanRequestView()


def build_bot(
    config: DiscordBotConfig,
    *,
    workspace: str | Path,
    bindings: InMemoryDiscordBindingStore | SQLiteDiscordBindingStore | None = None,
    authorizer: DiscordAuthorizer | None = None,
    on_event=None,
    outbound: DiscordOutboundPublisher | None = None,
    human: DiscordHumanAdapter | None = None,
    binding_is_active=None,
    typing_delay_seconds: float = 2.0,
    human_facing_sender: DiscordHumanFacingSender | None = None,
    conversation_log: ConversationLog | None = None,
    history_seed_limit: int = 500,
    history_incremental_limit: int = 100,
    history_seed_days: int = 30,
    persistent_view_loader=None,
    intent_resolver=None,
) -> Any:
    """Build the Gateway bot; no Discord connection starts until ``run``."""

    if not isinstance(config, DiscordBotConfig):
        raise TypeError("config must be DiscordBotConfig")
    discord, commands = _discord_modules()
    authorizer = authorizer or DiscordAuthorizer(
        allowed_user_ids=config.allowed_user_ids,
        allowed_guild_ids=config.allowed_guild_ids,
        allowed_channel_ids=config.allowed_channel_ids,
    )
    if not isinstance(authorizer, DiscordAuthorizer):
        raise TypeError("authorizer must be DiscordAuthorizer")
    bindings = bindings or InMemoryDiscordBindingStore()
    if not isinstance(bindings, (InMemoryDiscordBindingStore, SQLiteDiscordBindingStore)):
        raise TypeError("bindings must implement the Discord binding contract")
    sender = human_facing_sender or DiscordHumanFacingSender(typing_delay_seconds=typing_delay_seconds)
    if not isinstance(sender, DiscordHumanFacingSender):
        raise TypeError("human_facing_sender must implement the Discord send boundary")
    if conversation_log is not None and not isinstance(conversation_log, ConversationLog):
        raise TypeError("conversation_log must implement the ConversationLog boundary")
    if persistent_view_loader is not None and not callable(persistent_view_loader):
        raise TypeError("persistent_view_loader must be callable")
    if intent_resolver is not None and not callable(intent_resolver):
        raise TypeError("intent_resolver must be callable")
    intent_resolver = intent_resolver or resolve_plain_text
    ingress = DiscordIngressAdapter(
        authorizer=authorizer,
        bindings=bindings,
        binding_is_active=binding_is_active,
        intent_resolver=intent_resolver,
    )
    scope = DiscordScope(Path(workspace))
    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
    sync_state = {"done": False}
    restore_state = {"done": False}
    outbound_state: dict[str, asyncio.Task[Any] | None] = {"task": None}

    async def _send_human_facing(
        channel: Any,
        content: str,
        *,
        binding_key: DiscordBindingKey,
        message_kind: str,
        reply_to_message_id: str | None = None,
        view: Any | None = None,
    ) -> Any:
        """Send once through the UI boundary, then append only sent messages."""

        sent_message = await sender.send(channel, content, view=view)
        sent_id = str(getattr(sent_message, "id", ""))
        if conversation_log is not None and sent_id.isdecimal():
            binding = bindings.lookup(binding_key)
            conversation_log.append(
                conversation_message_from_discord(
                    sent_message,
                    binding_key=binding_key,
                    message_kind=message_kind,
                    message_id=sent_id,
                    content=content,
                    direction="outbound",
                    root_id=None if binding is None else binding.root_id,
                    run_id=None if binding is None else binding.run_id,
                    reply_to_message_id=reply_to_message_id,
                )
            )
        return sent_message

    async def _serve_outbound() -> None:
        publisher = getattr(bot, "_dev_agent_discord_outbound", None)
        if publisher is None:
            return
        try:
            await publisher.serve(stop=bot.is_closed)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Outbound delivery is a read-only projection.  A Discord or
            # SQLite observation error must not terminate Gateway ingress.
            return

    @bot.event
    async def on_ready():
        if not sync_state["done"]:
            try:
                # Command registration is useful UI setup, but it must not
                # hold Gateway ingress hostage.  A REST-side delay or
                # transient denial is observable as bounded setup state; the
                # bot can still receive ordinary messages through the already
                # connected Gateway and the next process start retries sync.
                await asyncio.wait_for(bot.tree.sync(), timeout=_COMMAND_SYNC_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                print("Discord application-command sync timed out; Gateway ingress remains active")
            except Exception:
                print("Discord application-command sync failed; Gateway ingress remains active")
            sync_state["done"] = True
        if persistent_view_loader is not None and not restore_state["done"]:
            try:
                views = persistent_view_loader()
                if inspect.isawaitable(views):
                    views = await views
                for item in views or ():
                    view, message_id = item
                    if message_id is None:
                        bot.add_view(view)
                    else:
                        bot.add_view(view, message_id=int(message_id))
            except Exception:
                print("Discord persistent component restoration failed")
            finally:
                restore_state["done"] = True
        if outbound_state["task"] is None:
            publisher = getattr(bot, "_dev_agent_discord_outbound", None)
            if publisher is not None:
                outbound_state["task"] = asyncio.create_task(_serve_outbound())
        print("Discord Human UI bot ready")

    @bot.event
    async def on_message(message):
        if getattr(message.author, "bot", False):
            return
        try:
            projected_message = _message_from_discord(message)
        except ValueError:
            # Discord itself supplied malformed/unusable metadata; do not echo
            # or route it into the Core boundary.
            return
        binding_key = _binding_key_from_discord(getattr(message, "guild", None), getattr(message, "channel", None))
        # Reject unauthorized input before reading any channel history.  This
        # keeps the bounded context query behind the same parent-channel
        # authorization boundary as normal ingress and components.
        if not authorizer.is_allowed(projected_message.author_id, binding_key.guild_id, binding_key.channel_id):
            return
        if human is not None and projected_message.reference_message_id:
            request_id = bindings.request_id_for_delivery(projected_message.reference_message_id)
            if request_id is not None:
                try:
                    human.receive_response(
                        request_id=request_id,
                        author_id=projected_message.author_id,
                        response=projected_message.content,
                        decision=None,
                        guild_id=projected_message.guild_id,
                        channel_id=projected_message.channel_id,
                    )
                    if not bindings.mark_message_seen(
                        projected_message.message_id,
                        binding_key=DiscordBindingKey(
                            projected_message.guild_id,
                            projected_message.channel_id,
                            projected_message.thread_id,
                        ),
                        kind="HUMAN_RESPONSE",
                    ):
                        return
                except (PermissionError, ValueError, KeyError):
                    return
                if conversation_log is not None:
                    conversation_log.append(
                        conversation_message_from_discord(
                            message,
                            binding_key=DiscordBindingKey(
                                projected_message.guild_id,
                                projected_message.channel_id,
                                projected_message.thread_id,
                            ),
                            message_kind="HUMAN_RESPONSE",
                            message_id=projected_message.message_id,
                            content=projected_message.content,
                            direction="inbound",
                        )
                    )
                acknowledgement = "回答を受け付けました。"
                await _send_human_facing(
                    message.channel,
                    acknowledgement,
                    binding_key=DiscordBindingKey(
                        projected_message.guild_id,
                        projected_message.channel_id,
                        projected_message.thread_id,
                    ),
                    message_kind="HUMAN_RESPONSE_ACK",
                    reply_to_message_id=projected_message.message_id,
                )
                return
        history_context: tuple = ()
        try:
            if conversation_log is not None:
                await sync_discord_history(
                    message.channel,
                    conversation_log,
                    bindings,
                    binding_key,
                    authorizer=authorizer,
                    bot_user_id=str(getattr(getattr(bot, "user", None), "id", config.application_id)),
                    current_message_id=projected_message.message_id,
                    current_message=message,
                    history_after_factory=lambda value: discord.Object(id=value),
                    seed_limit=history_seed_limit,
                    incremental_limit=history_incremental_limit,
                    seed_days=history_seed_days,
                )
                history_context = build_bounded_context(
                    conversation_log,
                    binding_key,
                    current_message_id=projected_message.message_id,
                    reply_to_message_id=projected_message.reference_message_id,
                )
            else:
                history_context = await collect_discord_history(
                    message.channel,
                    current_message_id=projected_message.message_id,
                    current_message=message,
                    authorizer=authorizer,
                    guild_id=projected_message.guild_id,
                    channel_id=projected_message.channel_id,
                    thread_id=projected_message.thread_id,
                    bot_user_id=str(getattr(getattr(bot, "user", None), "id", config.application_id)),
                )
        except Exception:
            # History is a bounded context hint.  A read failure must not turn
            # an otherwise accepted ingress into a retry or replay.
            history_context = ()
        try:
            event = ingress.accept(projected_message, history_context=history_context)
        except ValueError:
            # Discord itself supplied malformed/unusable metadata; do not echo
            # or route it into the Core boundary.
            return
        if event is None:
            # Duplicate messages are deliberately silent.
            return
        if conversation_log is not None:
            conversation_log.append(
                conversation_message_from_discord(
                    message,
                    binding_key=event.binding_key,
                    message_kind=event.kind.value,
                    message_id=projected_message.message_id,
                    content=projected_message.content,
                    direction="inbound",
                )
            )
        result = None
        if on_event is not None:
            result = on_event(event)
            if inspect.isawaitable(result):
                result = await result
        if event.kind is DiscordMessageKind.READ_QUERY and isinstance(result, Mapping):
            await _send_human_facing(
                message.channel,
                render_read_projection(result),
                binding_key=event.binding_key,
                message_kind="READ_QUERY_REPLY",
            )
        elif event.kind is DiscordMessageKind.CHAT and isinstance(result, Mapping):
            await _send_human_facing(
                message.channel,
                render_chat_response(result),
                binding_key=event.binding_key,
                message_kind="CHAT_REPLY",
            )
        else:
            await _send_human_facing(
                message.channel,
                render_ingress_ack(event.kind.value, result=result),
                binding_key=event.binding_key,
                message_kind=f"{event.kind.value}_ACK",
            )
        await bot.process_commands(message)

    @bot.tree.command(name="dir", description="作業対象ディレクトリを指定します")
    async def directory_command(interaction, path: str):
        binding_key = _binding_key_from_discord(interaction.guild, interaction.channel)
        if not authorizer.is_allowed(str(interaction.user.id), binding_key.guild_id, binding_key.channel_id):
            await interaction.response.send_message("この操作は許可されていません。", ephemeral=True)
            return
        try:
            resolved = scope.resolve_directory(path)
        except ValueError:
            await interaction.response.send_message("指定ディレクトリは許可されていません。", ephemeral=True)
            return
        key = binding_key
        existing = bindings.get_scope(key)
        bindings.save_scope(
            key,
            directory_scope=resolved,
            selected_files=() if existing is None else existing.selected_files,
        )
        await interaction.response.send_message(f"参照ディレクトリ: `{resolved}`", ephemeral=True)

    @bot.tree.command(name="file", description="参照ファイルを指定します")
    async def file_command(interaction, path: str):
        binding_key = _binding_key_from_discord(interaction.guild, interaction.channel)
        if not authorizer.is_allowed(str(interaction.user.id), binding_key.guild_id, binding_key.channel_id):
            await interaction.response.send_message("この操作は許可されていません。", ephemeral=True)
            return
        try:
            resolved = scope.resolve_file(path)
        except ValueError:
            await interaction.response.send_message("指定ファイルは許可されていません。", ephemeral=True)
            return
        key = binding_key
        existing = bindings.get_scope(key)
        selected_files = () if existing is None else existing.selected_files
        if resolved not in selected_files:
            selected_files = (*selected_files, resolved)
        bindings.save_scope(
            key,
            directory_scope=None if existing is None else existing.directory_scope,
            selected_files=selected_files,
        )
        await interaction.response.send_message(f"参照ファイル: `{resolved}`", ephemeral=True)

    bot._dev_agent_discord_ingress = ingress
    bot._dev_agent_discord_scope = scope
    bot._dev_agent_discord_bindings = bindings
    bot._dev_agent_discord_on_event = on_event
    bot._dev_agent_discord_intent_resolver = intent_resolver
    bot._dev_agent_discord_outbound = outbound
    bot._dev_agent_discord_sender = sender
    return bot


def run_from_environment(*, env_path: str | Path | None = None, workspace: str | Path | None = None) -> None:
    root = Path(__file__).resolve().parents[3]
    config = DiscordBotConfig.from_environment(env_path=env_path or root / ".env")
    workspace_path = Path(workspace or root).resolve()
    data_dir = os.environ.get("DEV_AGENT_DATA_DIR") or str(workspace_path / ".dev_agent")
    operation_config = OperationConfig.from_environment(data_dir=data_dir)
    authorizer = DiscordAuthorizer(
        allowed_user_ids=config.allowed_user_ids,
        allowed_guild_ids=config.allowed_guild_ids,
        allowed_channel_ids=config.allowed_channel_ids,
    )
    with DiscordRuntimeComposition.open(operation_config, authorizer=authorizer) as composition:
        conversation_log = ConversationLog(composition.store)
        advisory_service = None
        intent_resolver = resolve_plain_text
        # Use the canonical Operation/Provider dispatcher only when the
        # operator has explicitly configured a real provider or pool.  The
        # default fake Operation config must remain a deterministic, offline
        # smoke path rather than silently creating a model route.
        if operation_config.provider_id != "fake" or operation_config.provider_pool is not None:
            try:
                advisory_service = OperationService.open(operation_config)
                intent_resolver = ProposalOnlyIntentResolver(advisory_service.dispatcher.request)
            except Exception:
                # Resolver availability is advisory.  Do not expose provider
                # details or raw exception text through Discord logs; ingress
                # remains available through the deterministic fast path.
                if advisory_service is not None:
                    advisory_service.close()
                    advisory_service = None
                intent_resolver = resolve_plain_text
                print("Discord semantic intent resolver unavailable; deterministic fallback active")

        def _load_persistent_views():
            restored = []
            for request in composition.store.list_pending_human_requests():
                if composition.store.get_human_response(request.request_id) is not None:
                    continue
                for message_id in composition.bindings.delivery_message_ids(request.request_id):
                    restored.append(
                        (
                            build_human_request_view(
                                request=request,
                                authorizer=authorizer,
                                submit=composition.human.receive_response,
                            ),
                            message_id,
                        )
                    )
            for binding in composition.bindings.list_bindings():
                task = composition.store.load_task(binding.run_id)
                if task is None or str(getattr(task.status, "value", task.status)) != "waiting_approval":
                    continue
                event = composition.store.latest_event_for_task(binding.run_id)
                payload = event.get("payload") if isinstance(event, Mapping) else None
                approval_id = payload.get("approval_reference") if isinstance(payload, Mapping) else None
                if not isinstance(approval_id, str) or not approval_id.strip():
                    continue
                for message_id in composition.bindings.delivery_message_ids(f"approval:{approval_id.strip()}"):
                    restored.append(
                        (
                            build_approval_view(
                                approval_id=approval_id.strip(),
                                authorizer=authorizer,
                                submit=composition.submit_approval,
                            ),
                            message_id,
                        )
                    )
            return restored

        bot = build_bot(
            config,
            workspace=workspace_path,
            bindings=composition.bindings,
            authorizer=authorizer,
            on_event=composition.core.handle,
            human=composition.human,
            binding_is_active=composition.binding_is_active,
            conversation_log=conversation_log,
            persistent_view_loader=_load_persistent_views,
            intent_resolver=intent_resolver,
        )

        async def _send_to_binding(binding, content, view=None):
            target_id = binding.key.thread_id or binding.key.channel_id
            channel = bot.get_channel(int(target_id))
            if channel is None:
                channel = await bot.fetch_channel(int(target_id))
            return await bot._dev_agent_discord_sender.send(channel, content, view=view)

        bot._dev_agent_discord_outbound = DiscordOutboundPublisher(
            composition.store,
            composition.bindings,
            send=_send_to_binding,
            approval_view_factory=lambda approval_id, _binding: build_approval_view(
                approval_id=approval_id,
                authorizer=authorizer,
                submit=composition.submit_approval,
            ),
            human_request_view_factory=lambda request, _binding: build_human_request_view(
                request=request,
                authorizer=authorizer,
                submit=composition.human.receive_response,
            ),
            conversation_log=conversation_log,
            archive_maintenance=lambda: archive_eligible_messages(
                conversation_log,
                Path(data_dir) / "discord-archive",
            ),
        )
        bot._dev_agent_discord_composition = composition
        bot._dev_agent_discord_human = composition.human
        bot._dev_agent_discord_approval_factory = composition.build_approval_adapter
        try:
            bot.run(config.bot_token)
        finally:
            if advisory_service is not None:
                advisory_service.close()


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
    "build_human_request_view",
    "main",
    "run_from_environment",
]
