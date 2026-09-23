from pathlib import Path

import pytest

from src.dev_agent.discord.adapter import (
    DiscordIngressAdapter,
    DiscordMessage,
    DiscordMessageKind,
    DiscordScope,
    classify_message,
)
from src.dev_agent.discord.auth import DiscordAuthorizer
from src.dev_agent.discord.binding import (
    DiscordBindingKey,
    DiscordScopeState,
    InMemoryDiscordBindingStore,
)


def _message(content: str, *, message_id: str = "100", author_id: str = "42", bot: bool = False):
    return DiscordMessage(
        message_id=message_id,
        author_id=author_id,
        guild_id="10",
        channel_id="20",
        thread_id="30",
        content=content,
        author_is_bot=bot,
    )


def test_message_classification_keeps_ordinary_text_and_explicit_cancel_separate():
    assert classify_message("このファイルを確認して") is DiscordMessageKind.NEW_REQUEST
    assert classify_message("補足: ここは現状のまま") is DiscordMessageKind.NOTE
    assert classify_message("/status") is DiscordMessageKind.READ_QUERY
    assert classify_message("/parallel 別件を進めて") is DiscordMessageKind.PARALLEL
    assert classify_message("/interrupt 今すぐ確認") is DiscordMessageKind.INTERRUPT
    assert classify_message("/cancel") is DiscordMessageKind.CANCEL
    assert classify_message("停止して") is DiscordMessageKind.CANCEL


def test_plain_text_uses_active_run_context_but_explicit_commands_win():
    assert classify_message("READMEにも注意点追加して", active_run=True) is DiscordMessageKind.NOTE
    assert classify_message("/parallel 別案も進めて", active_run=True) is DiscordMessageKind.PARALLEL
    assert classify_message("/new 別rootで進めて", active_run=True) is DiscordMessageKind.NEW_REQUEST


def test_ingress_uses_injected_active_binding_observer():
    store = InMemoryDiscordBindingStore()
    key = DiscordBindingKey(guild_id="10", channel_id="20", thread_id="30")
    store.bind(key, root_id="root-1", run_id="run-1")
    adapter = DiscordIngressAdapter(
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
        bindings=store,
        binding_is_active=lambda candidate: candidate == key,
    )

    event = adapter.accept(_message("READMEにも注意点追加して"))

    assert event is not None
    assert event.kind is DiscordMessageKind.NOTE


def test_unbound_ingress_keeps_plain_text_as_new_request():
    adapter = DiscordIngressAdapter(
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
        bindings=InMemoryDiscordBindingStore(),
        binding_is_active=lambda _candidate: True,
    )

    event = adapter.accept(_message("このファイルを確認して"))

    assert event is not None
    assert event.kind is DiscordMessageKind.NEW_REQUEST


def test_ingress_ignores_bot_messages_and_duplicate_message_ids():
    adapter = DiscordIngressAdapter(
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
        bindings=InMemoryDiscordBindingStore(),
    )

    assert adapter.accept(_message("hello", bot=True)) is None
    accepted = adapter.accept(_message("hello"))
    assert accepted is not None
    assert adapter.accept(_message("hello again", message_id="100")) is None
    assert adapter.accept(_message("new", message_id="101")) is not None


def test_authorizer_uses_numeric_ids_not_display_names():
    authorizer = DiscordAuthorizer(
        allowed_user_ids={"42"},
        allowed_guild_ids={"10"},
        allowed_channel_ids={"20"},
    )

    assert authorizer.is_allowed("42", "10", "20") is True
    assert authorizer.is_allowed("display-name", "10", "20") is False
    assert authorizer.is_allowed("42", "11", "20") is False
    assert DiscordAuthorizer().is_allowed("42", "10", "20") is False


def test_scope_accepts_workspace_relative_normal_path_and_rejects_escape_or_protected():
    workspace = Path(__file__).resolve().parents[2]
    scope = DiscordScope(workspace)

    assert scope.resolve_file("src/dev_agent/providers/normalize.py") == "src/dev_agent/providers/normalize.py"
    assert scope.resolve_directory("src/dev_agent/providers") == "src/dev_agent/providers"

    with pytest.raises(ValueError):
        scope.resolve_file("../outside.txt")
    with pytest.raises(ValueError):
        scope.resolve_file("C:/Users/other/secret.txt")
    with pytest.raises(ValueError):
        scope.resolve_file("src/dev_agent/operation.py")
    with pytest.raises(ValueError):
        scope.resolve_file(".env")


def test_binding_store_keeps_only_channel_pointer_and_message_idempotency():
    store = InMemoryDiscordBindingStore()
    key = DiscordBindingKey(guild_id="10", channel_id="20", thread_id="30")

    store.bind(key, root_id="root-1", run_id="run-1")
    binding = store.lookup(key)
    assert binding is not None
    assert binding.root_id == "root-1"
    assert binding.run_id == "run-1"
    assert not hasattr(binding, "task_state")
    assert store.mark_message_seen("40") is True
    assert store.mark_message_seen("40") is False


def test_in_memory_scope_is_bounded_and_retrievable():
    store = InMemoryDiscordBindingStore()
    key = DiscordBindingKey(guild_id="10", channel_id="20", thread_id="30")

    store.save_scope(key, directory_scope="src/dev_agent", selected_files=("src/dev_agent/operation.py",))
    scope = store.get_scope(key)

    assert scope == DiscordScopeState(
        directory_scope="src/dev_agent",
        selected_files=("src/dev_agent/operation.py",),
    )
