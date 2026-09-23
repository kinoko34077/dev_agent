from __future__ import annotations

import asyncio
from uuid import uuid4

from src.dev_agent.discord.binding import DiscordBindingKey, SQLiteDiscordBindingStore
from src.dev_agent.discord.conversation_log import ConversationLog
from src.dev_agent.discord.outbound import DiscordOutboundPublisher
from src.dev_agent.domain.protocol import Event, Task, TaskStatus
from src.dev_agent.human import HumanRequest
from src.dev_agent.state.sqlite_store import SQLiteStateStore


def _binding(store: SQLiteStateStore, task: Task):
    bindings = SQLiteDiscordBindingStore(store)
    key = DiscordBindingKey("10", "20", "30")
    bindings.bind(key, root_id=task.root_task_id or task.task_id, run_id=task.task_id)
    return bindings, key


def _request(task: Task) -> HumanRequest:
    return HumanRequest(
        request_id="human-request-1",
        root_id=task.root_task_id or task.task_id,
        task_id=task.task_id,
        attempt_id="attempt-1",
        reason="仕様判断が必要です",
        question="AとBのどちらにしますか？",
        allowed_answers=("A", "B"),
    )


def test_human_request_is_pushed_once_and_survives_pump_reentry(tmp_path):
    sent: list[tuple[str, str, object | None]] = []

    async def send(binding, content, view=None):
        sent.append((binding.key.channel_id, content, view))
        return str(800 + len(sent))

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        task = Task(objective="request")
        store.save_task(task)
        bindings, _key = _binding(store, task)
        store.save_human_request(_request(task))
        publisher = DiscordOutboundPublisher(store, bindings, send=send)

        first = asyncio.run(publisher.publish_once())
        second = asyncio.run(publisher.publish_once())

        assert first["human_requests"] == 1
        assert first["progress"] == 1
        assert second["human_requests"] == 0
        assert len(sent) == 2
        assert "判断が必要です" in sent[0][1]


def test_human_request_projection_can_attach_finite_answer_view(tmp_path):
    sent: list[tuple[str, object | None]] = []

    async def send(_binding, content, view=None):
        sent.append((content, view))
        return "850"

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        task = Task(objective="request-with-view")
        store.save_task(task)
        bindings, _key = _binding(store, task)
        store.save_human_request(_request(task))
        publisher = DiscordOutboundPublisher(
            store,
            bindings,
            send=send,
            human_request_view_factory=lambda request, _binding: {"request_id": request.request_id},
        )

        result = asyncio.run(publisher.publish_once())

        assert result["human_requests"] == 1
        assert sent[0][1] == {"request_id": "human-request-1"}


def test_latest_progress_is_projected_without_replaying_older_events(tmp_path):
    sent: list[str] = []

    async def send(_binding, content, view=None):
        sent.append(content)
        return str(900 + len(sent))

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        task = Task(objective="progress")
        store.save_task(task)
        bindings, _key = _binding(store, task)
        store.append_event(
            Event(
                event_id=str(uuid4()),
                event_type="task.started",
                task_id=task.task_id,
                payload={"source": "runtime"},
            )
        )
        task.status = TaskStatus.COMPLETED
        store.save_task(task)
        latest = Event(
            event_id=str(uuid4()),
            event_type="task.completed",
            task_id=task.task_id,
            payload={"source": "runtime"},
        )
        store.append_event(latest)
        publisher = DiscordOutboundPublisher(store, bindings, send=send)

        first = asyncio.run(publisher.publish_once())
        second = asyncio.run(publisher.publish_once())

        assert first["progress"] == 1
        assert second["progress"] == 0
        assert len(sent) == 1
        assert "完了" in sent[0]


def test_final_response_is_projected_once_and_recorded_after_send(tmp_path):
    sent: list[str] = []

    async def send(_binding, content, view=None):
        sent.append(content)
        return str(950 + len(sent))

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        task = Task(objective="final response")
        task.status = TaskStatus.COMPLETED
        store.save_task(task)
        bindings, _key = _binding(store, task)
        store.append_event(
            Event(
                event_id=str(uuid4()),
                event_type="task.completed",
                task_id=task.task_id,
                payload={"text_segments": ["READMEを確認しました。", "問題ありません。"]},
            )
        )
        publisher = DiscordOutboundPublisher(
            store,
            bindings,
            send=send,
            conversation_log=ConversationLog(store),
        )

        first = asyncio.run(publisher.publish_once())
        second = asyncio.run(publisher.publish_once())

        assert first["final_responses"] == 1
        assert second["final_responses"] == 0
        assert sent[-1] == "READMEを確認しました。\n問題ありません。"
        rows = ConversationLog(store).list_context("10|20|30")
        assert rows[-1].message_id == "952"
        assert rows[-1].direction == "outbound"
        assert rows[-1].content == sent[-1]


def test_final_response_accepts_production_task_completed_text_payload(tmp_path):
    sent: list[str] = []

    async def send(_binding, content, view=None):
        sent.append(content)
        return str(960 + len(sent))

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        task = Task(objective="production completion payload")
        task.status = TaskStatus.COMPLETED
        store.save_task(task)
        bindings, _key = _binding(store, task)
        store.append_event(
            Event(
                event_id=str(uuid4()),
                event_type="task.completed",
                task_id=task.task_id,
                payload={"text": ["実際の完了回答です。", "次の処理はありません。"]},
            )
        )
        publisher = DiscordOutboundPublisher(store, bindings, send=send)

        result = asyncio.run(publisher.publish_once())

        assert result["final_responses"] == 1
        assert sent[-1] == "実際の完了回答です。\n次の処理はありません。"
        assert sent.count("実際の完了回答です。\n次の処理はありません。") == 1


def test_approval_projection_requires_an_explicit_core_submit_boundary(tmp_path):
    sent: list[tuple[str, object | None]] = []

    async def send(_binding, content, view=None):
        sent.append((content, view))
        return "901"

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        task = Task(objective="approval")
        store.save_task(task)
        bindings, _key = _binding(store, task)
        task.status = TaskStatus.WAITING_APPROVAL
        store.save_task(task)
        store.append_event(
            Event(
                event_id=str(uuid4()),
                event_type="task.waiting_approval",
                task_id=task.task_id,
                payload={"approval_reference": "approval-1"},
            )
        )

        without_boundary = DiscordOutboundPublisher(store, bindings, send=send)
        result = asyncio.run(without_boundary.publish_once())

        assert result["approvals"] == 0
        assert len(sent) == 1
        assert sent[0][1] is None

        with_boundary = DiscordOutboundPublisher(
            store,
            bindings,
            send=send,
            approval_view_factory=lambda approval_id, _binding: {"approval_id": approval_id},
        )
        result = asyncio.run(with_boundary.publish_once())

        assert result["approvals"] == 1
        assert sent[1][1] == {"approval_id": "approval-1"}


def test_projection_loop_survives_transient_observation_error(tmp_path):
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        bindings = SQLiteDiscordBindingStore(store)
        publisher = DiscordOutboundPublisher(store, bindings, send=lambda *_args: "902")
        attempts = {"publish": 0, "stop": 0}

        async def publish_once():
            attempts["publish"] += 1
            if attempts["publish"] == 1:
                raise RuntimeError("transient observation failure")
            return {"human_requests": 0, "approvals": 0, "progress": 0}

        def stop():
            attempts["stop"] += 1
            return attempts["stop"] > 2

        publisher.publish_once = publish_once
        asyncio.run(publisher.serve(stop=stop, interval_seconds=0.001))

        assert attempts["publish"] == 2


def test_projection_loop_exposes_bounded_error_health_without_raw_exception(tmp_path):
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        bindings = SQLiteDiscordBindingStore(store)
        publisher = DiscordOutboundPublisher(store, bindings, send=lambda *_args: "903")
        attempts = {"publish": 0, "stop": 0}

        async def publish_once():
            attempts["publish"] += 1
            raise RuntimeError("secret payload must not be projected")

        def stop():
            attempts["stop"] += 1
            return attempts["stop"] > 1

        publisher.publish_once = publish_once
        asyncio.run(publisher.serve(stop=stop, interval_seconds=0.001))

        assert publisher.health_projection() == {
            "state": "DEGRADED",
            "last_error_category": "RuntimeError",
        }


def test_projection_pass_runs_existing_archive_maintenance_callback(tmp_path):
    calls: list[str] = []

    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        bindings = SQLiteDiscordBindingStore(store)
        publisher = DiscordOutboundPublisher(
            store,
            bindings,
            send=lambda *_args: "904",
            archive_maintenance=lambda: calls.append("archive"),
        )

        asyncio.run(publisher.publish_once())

    assert calls == ["archive"]
