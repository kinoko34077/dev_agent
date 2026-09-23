from __future__ import annotations

from src.dev_agent.discord.adapter import DiscordIngressEvent, DiscordMessage, DiscordMessageKind
from src.dev_agent.discord.auth import DiscordAuthorizer
from src.dev_agent.discord.binding import DiscordBindingKey
from src.dev_agent.discord.composition import DiscordRuntimeComposition
from src.dev_agent.operation import OperationConfig
from src.dev_agent.coordination.protocol import MessageKind


def _event(content: str, kind: DiscordMessageKind, message_id: str) -> DiscordIngressEvent:
    message = DiscordMessage(
        message_id=message_id,
        author_id="42",
        guild_id="10",
        channel_id="20",
        thread_id="30",
        content=content,
    )
    return DiscordIngressEvent(
        message=message,
        kind=kind,
        binding_key=DiscordBindingKey("10", "20", "30"),
    )


def test_standard_composition_submits_durable_operation_and_binds_pointer(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        task = composition.core.handle(
            _event("小さな確認作業", DiscordMessageKind.NEW_REQUEST, "1001")
        )

        binding = composition.bindings.lookup(DiscordBindingKey("10", "20", "30"))
        assert binding is not None
        assert binding.root_id == task.root_task_id
        assert binding.run_id == task.task_id
        assert composition.store.load_task(task.task_id).objective == "小さな確認作業"


def test_standard_composition_reads_existing_operation_without_submitting_task(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        task = composition.core.handle(
            _event("状態確認対象", DiscordMessageKind.NEW_REQUEST, "1002")
        )
        status = composition.core.handle(
            _event("今何してる", DiscordMessageKind.READ_QUERY, "1003")
        )

        assert status["task_id"] == task.task_id
        assert status["state"] == "queued"
        assert len(composition.store.snapshot()["tasks"]) == 1


def test_intervention_uses_existing_coordination_note_with_bounded_content(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    content = "補足: 確認して api_key=do-not-persist-this"
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        result = composition.core.handle(
            _event(content, DiscordMessageKind.NOTE, "1004")
        )
        snapshot = composition.coordination.snapshot(recipient_role="agent")

        assert result.kind is MessageKind.NOTE
        assert len(snapshot.mailbox) == 1
        assert "discord:NOTE:1004" in snapshot.mailbox[0].subject
        assert "do-not-persist-this" not in snapshot.mailbox[0].subject
        assert content not in snapshot.mailbox[0].subject
