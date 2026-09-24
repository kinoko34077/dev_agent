from __future__ import annotations

from src.dev_agent.discord.adapter import (
    DiscordIngressAdapter,
    DiscordIngressEvent,
    DiscordMessage,
    DiscordMessageKind,
)
from src.dev_agent.discord.auth import DiscordAuthorizer
from src.dev_agent.discord.binding import DiscordBindingKey
from src.dev_agent.discord.composition import DiscordRuntimeComposition
from src.dev_agent.discord.history import DiscordHistoryMessage
from src.dev_agent.discord.conversation_archive import archive_eligible_messages
from src.dev_agent.discord.conversation_log import ConversationLog, ConversationMessage
from src.dev_agent.discord.intent import IntentKind, IntentProposal
from src.dev_agent.domain.protocol import Task, TaskStatus
from src.dev_agent.operation import OperationConfig, OperationService
from src.dev_agent.operation_runtime import RuntimeCoordinator
from src.dev_agent.coordination.protocol import MessageKind
from src.dev_agent.runtime.state import RuntimeState
from src.dev_agent.scheduler.queue import DurableQueue
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from time import time
from datetime import datetime, timezone


def _event(
    content: str,
    kind: DiscordMessageKind,
    message_id: str,
    *,
    history_context: tuple[DiscordHistoryMessage, ...] = (),
) -> DiscordIngressEvent:
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
        history_context=history_context,
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


def test_terminal_follow_up_starts_new_operation_with_previous_pointer_and_context(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    previous = Task(objective="previous", status=TaskStatus.COMPLETED)
    event = _event(
        "やっぱりさっきの2個目だけ戻して",
        DiscordMessageKind.NEW_REQUEST,
        "1002",
        history_context=(DiscordHistoryMessage(role="assistant", content="2件を統合しました", message_id="1001"),),
    )
    event = DiscordIngressEvent(
        message=event.message,
        kind=event.kind,
        binding_key=event.binding_key,
        history_context=event.history_context,
        intent_proposal=IntentProposal(IntentKind.FOLLOW_UP, event.message.content),
    )
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        composition.store.save_task(previous)
        composition.bindings.bind(event.binding_key, root_id=previous.task_id, run_id=previous.task_id)
        task = composition.core.handle(event)

        assert task.inputs["discord_follow_up"] == {
            "previous_root_id": previous.task_id,
            "previous_run_id": previous.task_id,
        }
        assert task.inputs["discord_context"]["messages"][0]["message_id"] == "1001"


def test_chat_uses_bounded_archive_search_only_for_past_reference(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        log = ConversationLog(composition.store)
        log.append(
            ConversationMessage(
                message_id="1800",
                binding_key="10|20|30",
                guild_id="10",
                channel_id="20",
                thread_id="30",
                created_at="2026-08-01T00:00:00+00:00",
                received_at="2026-08-01T00:00:00+00:00",
                speaker_role="assistant",
                speaker_id="99",
                speaker_name="dev_agent",
                direction="outbound",
                content="READMEのエラー処理を修正しました。",
                reply_to_message_id=None,
                message_kind="FINAL",
                root_id=None,
                run_id=None,
                source="discord",
            )
        )
        archive_eligible_messages(
            log,
            config.data_dir / "discord-archive",
            now=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        result = composition.chat_response(
            _event("前にREADMEで何を変えた？", DiscordMessageKind.CHAT, "1801")
        )

    assert result["state"] == "CHAT"
    assert "README" in result["text"]


def test_standard_composition_submits_user_wait_without_claiming_a_worker(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    event = _event("20秒待ってから返事して", DiscordMessageKind.WAIT, "1001")
    event = DiscordIngressEvent(
        message=event.message,
        kind=event.kind,
        binding_key=event.binding_key,
        intent_proposal=IntentProposal(IntentKind.WAIT, event.message.content, 20),
    )
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        task = composition.core.handle(event)

        assert task.status is TaskStatus.WAITING_DEPENDENCY
        with DurableQueue(config.queue_path) as queue:
            assert queue.snapshot(task.task_id).state == "waiting"
        assert composition.store.load_task(task.task_id).metadata["wait_reason"] == "user_delay"


def test_active_wait_is_explicitly_deferred_instead_of_becoming_note(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    waiting = Task(objective="active work", status=TaskStatus.WAITING_HUMAN)
    key = DiscordBindingKey("10", "20", "30")
    event = _event("20秒待ってから返事して", DiscordMessageKind.WAIT, "1009")
    event = DiscordIngressEvent(
        message=event.message,
        kind=event.kind,
        binding_key=event.binding_key,
        intent_proposal=IntentProposal(IntentKind.WAIT, event.message.content, 20),
    )
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        composition.store.save_task(waiting)
        composition.bindings.bind(key, root_id=waiting.task_id, run_id=waiting.task_id)

        result = composition.core.handle(event)

        assert result == {
            "state": "WAIT_DEFERRED",
            "delay_seconds": 20,
            "reason": "active_run_requires_safe_checkpoint",
        }
        assert composition.coordination.snapshot(recipient_role="agent").mailbox == ()
        assert composition.store.load_task(waiting.task_id).status is TaskStatus.WAITING_HUMAN


def test_active_wait_is_parked_at_a_cooperative_checkpoint_and_not_before_due(tmp_path):
    config = OperationConfig(
        data_dir=tmp_path / "agent",
        provider_id="fake",
        model="deterministic",
        worker_id="active-wait-runtime-worker",
        idle_sleep_seconds=0.01,
    )
    active = Task(objective="active work", status=TaskStatus.RUNNING)
    with SQLiteStateStore(config.state_path) as store:
        store.save_task(active)
    with DurableQueue(config.queue_path) as queue:
        queue.enqueue(active.task_id)

    requested = OperationService.request_user_delay(
        config,
        active.task_id,
        20,
        source="20秒待ってから返事して",
    )

    assert requested["state"] == "WAIT_ACCEPTED"
    assert requested["wake_at_epoch"] >= requested["accepted_at_epoch"] + 20

    with DurableQueue(config.queue_path) as queue:
        item = queue.snapshot(active.task_id)
        assert item.state == "waiting"
        assert item.wake_at is not None
        assert item.wake_at.timestamp() >= requested["accepted_at_epoch"] + 20
        assert queue.wake_due(now=requested["wake_at_epoch"] - 0.1, reason="user_delay") == 0
        assert queue.wake_due(now=requested["wake_at_epoch"] + 0.1, reason="user_delay") == 1


def test_active_leased_wait_is_consumed_by_controller_at_checkpoint(tmp_path):
    config = OperationConfig(
        data_dir=tmp_path / "agent",
        provider_id="fake",
        model="deterministic",
        worker_id="active-wait-controller-worker",
        idle_sleep_seconds=0.01,
    )
    active = Task(objective="leased active work", status=TaskStatus.RUNNING)
    with SQLiteStateStore(config.state_path) as store:
        store.save_task(active)
    with DurableQueue(config.queue_path) as queue:
        queue.enqueue(active.task_id)
        lease = queue.claim(config.worker_id, lease_seconds=30.0)

    requested = OperationService.request_user_delay(
        config,
        active.task_id,
        20,
        source="20秒待ってから返事して",
    )
    assert requested["state"] == "WAIT_DEFERRED"

    from src.dev_agent.operation_runtime import RuntimeCoordinator

    with RuntimeCoordinator.open(
        config,
        revision="active-wait-controller-test",
        instance_id="active-wait-controller-runtime",
    ) as runtime:
        parked = runtime.operation.controller.resume(active.task_id)
        assert parked.status is TaskStatus.WAITING_DEPENDENCY

    with DurableQueue(config.queue_path) as queue:
        item = queue.defer_until(
            active.task_id,
            worker_id=config.worker_id,
            state_version=lease.state_version,
            wake_at=requested["wake_at_epoch"],
            reason="user_delay",
        )
        assert item.state == "waiting"
        assert item.wake_at.timestamp() >= requested["accepted_at_epoch"] + 20


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


def test_composition_passes_bounded_history_as_context_without_replaying_it(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    history = (
        DiscordHistoryMessage(role="human", content="先にREADMEを確認して"),
        DiscordHistoryMessage(role="assistant", content="確認を開始します"),
    )
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        task = composition.core.handle(
            _event("それも反映して", DiscordMessageKind.NEW_REQUEST, "1006", history_context=history)
        )

        persisted = composition.store.load_task(task.task_id)
        assert persisted is not None
        assert persisted.inputs["discord_context"] == {
            "messages": [
                {"role": "human", "content": "先にREADMEを確認して"},
                {"role": "assistant", "content": "確認を開始します"},
            ]
        }
        assert len(composition.store.snapshot()["tasks"]) == 1
        assert composition.coordination.snapshot(recipient_role="agent").mailbox == ()


def test_waiting_binding_plain_text_is_one_note_without_new_root_or_cancellation(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    waiting = Task(objective="wait for the human answer", status=TaskStatus.WAITING_HUMAN)
    key = DiscordBindingKey("10", "20", "30")
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        composition.store.save_task(waiting)
        composition.bindings.bind(key, root_id=waiting.task_id, run_id=waiting.task_id)
        ingress = DiscordIngressAdapter(
            authorizer=composition.authorizer,
            bindings=composition.bindings,
            binding_is_active=composition.binding_is_active,
        )
        event = ingress.accept(
            DiscordMessage(
                message_id="1007",
                author_id="42",
                guild_id="10",
                channel_id="20",
                thread_id="30",
                content="あとREADMEにも反映して",
            )
        )

        assert event is not None
        assert event.kind is DiscordMessageKind.NOTE
        result = composition.core.handle(event)

        assert result.kind is MessageKind.NOTE
        assert len(composition.store.snapshot()["tasks"]) == 1
        assert composition.store.load_task(waiting.task_id).status is TaskStatus.WAITING_HUMAN
        mailbox = composition.coordination.snapshot(recipient_role="agent").mailbox
        assert len(mailbox) == 1
        assert mailbox[0].kind is MessageKind.NOTE


def test_note_attaches_history_as_immutable_coordination_context(tmp_path):
    config = OperationConfig(data_dir=tmp_path / "agent")
    history = (
        DiscordHistoryMessage(role="human", content="READMEのエラー処理を直して"),
        DiscordHistoryMessage(role="assistant", content="作業を開始します"),
    )
    with DiscordRuntimeComposition.open(
        config,
        authorizer=DiscordAuthorizer(allowed_user_ids={"42"}),
    ) as composition:
        result = composition.core.handle(
            _event(
                "さっきの方にコメントも付けて",
                DiscordMessageKind.NOTE,
                "1008",
                history_context=history,
            )
        )

        assert result.kind is MessageKind.NOTE
        mailbox = composition.coordination.snapshot(recipient_role="agent").mailbox
        assert len(mailbox) == 1
        assert len(mailbox[0].artifact_refs) == 1
        context = composition.coordination.artifacts.read_json(mailbox[0].artifact_refs[0])
        assert context["messages"] == [
            {"role": "human", "content": "READMEのエラー処理を直して"},
            {"role": "assistant", "content": "作業を開始します"},
        ]


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
