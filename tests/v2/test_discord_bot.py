import asyncio
from pathlib import Path

import pytest

from src.dev_agent.discord.bot import (
    DiscordConfigurationError,
    DiscordDependencyError,
    DiscordBotConfig,
    build_bot,
    build_approval_view,
    _message_from_discord,
)
from src.dev_agent.discord.auth import DiscordAuthorizer
from src.dev_agent.discord.binding import DiscordBindingKey, SQLiteDiscordBindingStore
from src.dev_agent.discord.human import DiscordHumanAdapter
from src.dev_agent.discord.renderer import (
    render_echo,
    render_human_request,
    render_progress,
)
from src.dev_agent.human import HumanRequest
from src.dev_agent.human import SQLiteHumanInteractionPort
from src.dev_agent.state.sqlite_store import SQLiteStateStore


class _NoopTyping:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


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

        def typing(self):
            return _NoopTyping()

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

        def typing(self):
            return _NoopTyping()

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

    assert message.channel.sent == ["現在の状態\n\n- 処理: Host Verification"]


def test_new_request_attaches_bounded_history_without_replaying_it(tmp_path):
    pytest.importorskip("discord")
    config = DiscordBotConfig.from_environment(env_path=_env_file(tmp_path))
    routed = []
    bot = build_bot(
        config,
        workspace=tmp_path,
        typing_delay_seconds=0,
        on_event=lambda event: routed.append(event),
    )
    bot.process_commands = lambda _message: asyncio.sleep(0)

    class _Author:
        id = 42
        bot = False

    class _Guild:
        id = 10

    class _Channel:
        id = 20

        def __init__(self):
            self.sent = []
            self.history_kwargs = None

        def typing(self):
            return _NoopTyping()

        async def send(self, content):
            self.sent.append(content)

        def history(self, **kwargs):
            self.history_kwargs = kwargs

            class _PreviousMessage:
                id = 900
                author = _Author()
                content = "先にREADMEを確認して"
                channel = _Channel()

            async def _iterate():
                yield _PreviousMessage()

            return _iterate()

    class _Message:
        id = 901
        author = _Author()
        guild = _Guild()
        content = "それも反映して"

        def __init__(self):
            self.channel = _Channel()

    message = _Message()
    asyncio.run(bot.on_message(message))

    assert len(routed) == 1
    assert [item.to_dict() for item in routed[0].history_context] == [
        {"role": "human", "content": "先にREADMEを確認して"}
    ]
    assert message.channel.history_kwargs["before"] is message
    assert len(message.channel.sent) == 1


def test_message_projection_preserves_reply_reference_id():
    class _Reference:
        message_id = 700

    class _Author:
        id = 42
        bot = False

    class _Guild:
        id = 10

    class _Channel:
        id = 20

    class _Message:
        id = 701
        author = _Author()
        guild = _Guild()
        channel = _Channel()
        content = "A"
        reference = _Reference()

    projected = _message_from_discord(_Message())

    assert projected.reference_message_id == "700"


def test_message_projection_uses_thread_parent_as_channel_pointer():
    class Thread:
        id = 701
        parent_id = 20

    class _Author:
        id = 42
        bot = False

    class _Guild:
        id = 10

    class _Message:
        id = 702
        author = _Author()
        guild = _Guild()
        channel = Thread()
        content = "A"
        reference = None

    projected = _message_from_discord(_Message())

    assert projected.channel_id == "20"
    assert projected.thread_id == "701"


def test_human_request_reply_is_correlated_before_normal_ingress(tmp_path):
    pytest.importorskip("discord")
    config = DiscordBotConfig.from_environment(env_path=_env_file(tmp_path))
    state_path = tmp_path / "state.sqlite3"
    request = HumanRequest(
        request_id="discord-request-1",
        root_id="root-1",
        task_id="task-1",
        attempt_id="attempt-1",
        reason="仕様判断",
        question="AかBか",
        allowed_answers=("A", "B"),
    )
    with SQLiteStateStore(state_path) as store:
        bindings = SQLiteDiscordBindingStore(store)
        key = DiscordBindingKey("10", "20", "")
        bindings.bind(key, root_id="root-1", run_id="task-1")
        human = DiscordHumanAdapter(
            SQLiteHumanInteractionPort(store),
            authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
            bindings=bindings,
        )
        human.request_human(request)
        bindings.record_delivery(request.request_id, "700")
        bot = build_bot(
            config,
            workspace=tmp_path,
            bindings=bindings,
            authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
            human=human,
        )
        bot.process_commands = lambda _message: asyncio.sleep(0)
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

            def typing(self):
                return _NoopTyping()

            async def send(self, content):
                self.sent.append(content)

        class _Reference:
            message_id = 700

        class _Message:
            id = 702
            author = _Author()
            guild = _Guild()
            content = "A"
            reference = _Reference()

            def __init__(self):
                self.channel = _Channel()

        message = _Message()
        asyncio.run(handler(message))

        assert message.channel.sent == ["回答を受け付けました。"]
        assert store.get_human_response(request.request_id).decision == "A"


def test_scope_commands_persist_scope_for_the_current_channel(tmp_path):
    pytest.importorskip("discord")
    config = DiscordBotConfig.from_environment(env_path=_env_file(tmp_path))
    workspace = Path(__file__).resolve().parents[2]
    bot = build_bot(config, workspace=workspace)
    commands = {command.name: command for command in bot.tree.get_commands()}

    class _User:
        id = 42

    class _Guild:
        id = 10

    class _Channel:
        id = 20

    class _Response:
        def __init__(self):
            self.messages = []

        async def send_message(self, content, **_kwargs):
            self.messages.append(content)

    class _Interaction:
        user = _User()
        guild = _Guild()
        channel = _Channel()

        def __init__(self):
            self.response = _Response()

    interaction = _Interaction()
    asyncio.run(commands["dir"].callback(interaction, "src/dev_agent"))
    asyncio.run(commands["file"].callback(interaction, "src/dev_agent/discord/bot.py"))

    scope = bot._dev_agent_discord_bindings.get_scope(DiscordBindingKey("10", "20", ""))
    assert scope.directory_scope == "src/dev_agent"
    assert scope.selected_files == ("src/dev_agent/discord/bot.py",)


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
