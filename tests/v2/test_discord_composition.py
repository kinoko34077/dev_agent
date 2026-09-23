from __future__ import annotations

from src.dev_agent.discord.adapter import DiscordIngressEvent, DiscordMessage, DiscordMessageKind
from src.dev_agent.discord.auth import DiscordAuthorizer
from src.dev_agent.discord.binding import DiscordBindingKey
from src.dev_agent.discord.composition import DiscordRuntimeComposition
from src.dev_agent.domain.protocol import Task, TaskStatus
from src.dev_agent.operation import OperationConfig, OperationService
from src.dev_agent.operation_runtime import RuntimeCoordinator
from src.dev_agent.coordination.protocol import MessageKind
from src.dev_agent.runtime.state import RuntimeState
from src.dev_agent.scheduler.queue import DurableQueue
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from time import time


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


def test_composition_passes_persisted_discord_scope_as_structured_task_input(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        key = DiscordBindingKey("10", "20", "30")
        composition.bindings.save_scope(
            key,
            directory_scope="src/dev_agent",
            selected_files=("src/dev_agent/operation.py",),
        )
        task = composition.core.handle(_event("スコープ付き依頼", DiscordMessageKind.NEW_REQUEST, "1005"))

        assert composition.store.load_task(task.task_id).inputs["discord_scope"] == {
            "directory": "src/dev_agent",
            "files": ["src/dev_agent/operation.py"],
        }


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


def test_parallel_intervention_creates_core_child_instead_of_collapsing_to_note(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        parent = composition.core.handle(
            _event("親作業", DiscordMessageKind.NEW_REQUEST, "1010")
        )
        child = composition.core.handle(
            _event("/parallel 別案の確認", DiscordMessageKind.PARALLEL, "1011")
        )

        assert child.parent_task_id == parent.task_id
        assert child.root_task_id == parent.root_task_id
        assert child.objective == "別案の確認"


def test_interrupt_intervention_keeps_distinct_mailbox_kind(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        composition.core.handle(_event("親作業", DiscordMessageKind.NEW_REQUEST, "1020"))
        result = composition.core.handle(
            _event("/interrupt 先に確認", DiscordMessageKind.INTERRUPT, "1021")
        )
        snapshot = composition.coordination.snapshot(recipient_role="agent")

        assert result.kind is MessageKind.INTERRUPT
        assert snapshot.mailbox[0].kind is MessageKind.INTERRUPT


def test_interrupt_mailbox_is_consumed_by_runtime_and_parent_resumes(tmp_path):
    config = OperationConfig(
        data_dir=tmp_path / "agent",
        provider_id="fake",
        model="deterministic",
        worker_id="interrupt-runtime-worker",
        idle_sleep_seconds=0.01,
    )
    parent = Task(objective="resume the parent after the interruption")
    state = RuntimeState.initial(parent, now=time()).to_checkpoint()
    with SQLiteStateStore(config.state_path) as store:
        store.save_task(parent)
        store.checkpoint(
            task_id=parent.task_id,
            step_id="11111111-1111-4111-8111-111111111111",
            phase="before_model",
            state=state,
        )
    with DurableQueue(config.queue_path) as queue:
        queue.enqueue(parent.task_id)

    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        key = DiscordBindingKey("10", "20", "30")
        composition.bindings.bind(key, root_id=parent.task_id, run_id=parent.task_id)
        result = composition.core.handle(
            _event("/interrupt 先に確認", DiscordMessageKind.INTERRUPT, "1022")
        )
        assert result.kind is MessageKind.INTERRUPT

    with RuntimeCoordinator.open(
        config,
        revision="interrupt-test-revision",
        instance_id="interrupt-runtime",
    ) as runtime:
        for _ in range(6):
            runtime.run_once()
            with SQLiteStateStore(config.state_path) as store:
                tasks = [
                    store.load_task(task_id)
                    for task_id in store.snapshot()["tasks"]
                ]
            if (
                any(task is not None and task.parent_task_id == parent.task_id and task.status is TaskStatus.COMPLETED for task in tasks)
                and OperationService.read_status(config, parent.task_id)["state"] == TaskStatus.COMPLETED.value
            ):
                break

    with SQLiteStateStore(config.state_path) as store:
        persisted_parent = store.load_task(parent.task_id)
        assert persisted_parent is not None
        assert persisted_parent.status is TaskStatus.COMPLETED
        assert persisted_parent.metadata["last_interrupt"]["resumed"] is True
        resumed_capsule = persisted_parent.metadata["last_interrupt"]["resume_capsule"]
        assert resumed_capsule["status"] == "RUNNING"
        assert resumed_capsule["interrupt_stack"]["frames"] == []
        assert any(
            event["event_type"] == "task.interrupt_resumed"
            for event in store.snapshot()["events"]
        )
        assert sum(
            1
            for payload in store.snapshot()["tasks"].values()
            if payload.get("parent_task_id") == parent.task_id
        ) == 1


def test_cancel_intervention_cancels_only_bound_task(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        task = composition.core.handle(_event("親作業", DiscordMessageKind.NEW_REQUEST, "1030"))
        result = composition.core.handle(
            _event("/cancel", DiscordMessageKind.CANCEL, "1031")
        )

        assert result["task_id"] == task.task_id
        assert result["state"] == "cancelled"


def test_core_approval_boundary_wakes_waiting_task_from_discord_adapter(tmp_path):
    config = OperationConfig(
        data_dir=tmp_path / "agent",
        provider_id="fake",
        model="deterministic",
        worker_id="approval-runtime-worker",
        idle_sleep_seconds=0.01,
    )
    task = Task(objective="approve a bounded tool call", status=TaskStatus.WAITING_APPROVAL)
    approval_id = "11111111-1111-4111-8111-111111111112"
    call_id = "11111111-1111-4111-8111-111111111113"
    state = RuntimeState.initial(task, now=time()).to_checkpoint()
    state["pending_tool_calls"] = [{
        "call_id": call_id,
        "tool_name": "publish",
        "arguments": {"value": "bounded"},
    }]
    state["approval_request"] = {
        "approval_reference": approval_id,
        "call_id": call_id,
        "tool_name": "publish",
        "side_effect_level": "external_write",
        "arguments_hash": "a" * 64,
    }
    with SQLiteStateStore(config.state_path) as store:
        store.save_task(task)
        store.checkpoint(
            task_id=task.task_id,
            step_id="11111111-1111-4111-8111-111111111114",
            phase="waiting_approval",
            state=state,
        )
    with DurableQueue(config.queue_path) as queue:
        queue.enqueue(task.task_id)
        lease = queue.claim("approval-runtime-worker", lease_seconds=30)
        queue.defer(
            task.task_id,
            worker_id="approval-runtime-worker",
            state_version=lease.state_version,
        )

    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        adapter = composition.build_approval_adapter(composition.submit_approval)
        result = adapter.submit(
            approval_id,
            author_id="42",
            approved=True,
            guild_id="10",
            channel_id="20",
        )

    assert result["state"] == TaskStatus.WAITING_APPROVAL.value
    with SQLiteStateStore(config.state_path) as store:
        assert store.has_approval(
            approval_id,
            task_id=task.task_id,
            side_effect_level="external_write",
            call_id=call_id,
            arguments_hash="a" * 64,
        )
        assert store.load_latest_checkpoint(task.task_id)["state"]["approval_id"] == approval_id
    with DurableQueue(config.queue_path) as queue:
        assert queue.snapshot(task.task_id).state == "queued"
