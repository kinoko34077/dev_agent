import json

import pytest

from src.dev_agent.domain.protocol import (
    Event,
    ExecutionLimits,
    ModelRequest,
    ModelResponse,
    ProtocolError,
    RecoveryTaskAuthority,
    Task,
    TaskClass,
    ToolCall,
    ToolResult,
    dumps,
)
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from src.dev_agent.state.json_store import JsonStateStore


def test_protocol_records_round_trip_without_provider_objects():
    task = Task(objective="simulate one harmless tool", inputs={"value": 2}, sensitivity="sensitive")
    request = ModelRequest(
        task_id=task.task_id,
        messages=[{"role": "user", "content": task.objective}],
        allowed_tools=["echo"],
    )
    call = ToolCall(
        tool_name="echo",
        arguments={"value": 2},
        originating_request_id=request.request_id,
    )
    response = ModelResponse(
        provider="fake",
        model="deterministic",
        tool_calls=[call],
        finish_reason="tool_call",
    )
    result = ToolResult(call_id=call.call_id, structured_result={"value": 2})
    event = Event(event_type="tool.completed", task_id=task.task_id, tool_call_id=call.call_id)

    assert Task.from_dict(task.to_dict()).to_dict() == task.to_dict()
    assert ModelRequest.from_dict(request.to_dict()).to_dict() == request.to_dict()
    assert ModelResponse.from_dict(response.to_dict()).to_dict() == response.to_dict()
    assert ToolResult.from_dict(result.to_dict()).to_dict() == result.to_dict()
    assert Event.from_dict(event.to_dict()).to_dict() == event.to_dict()
    assert json.loads(dumps(response))["provider"] == "fake"


def test_task_privacy_classification_is_durable_and_rejects_unknown_values():
    task = Task(objective="classified", sensitivity="internal")
    assert Task.from_dict(task.to_dict()).sensitivity == "internal"
    with pytest.raises(ProtocolError, match="sensitivity"):
        Task(objective="invalid", sensitivity="cloud")


def test_recovery_task_requires_authority_and_trusted_persisted_reload(tmp_path):
    with pytest.raises(ProtocolError, match="RecoveryTaskAuthority"):
        Task(objective="forged recovery", task_class=TaskClass.RECOVERY)
    task = RecoveryTaskAuthority.create(objective="authorized recovery")
    with pytest.raises(ProtocolError, match="RecoveryTaskAuthority"):
        Task.from_dict(task.to_dict())
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        store.save_task(task)
        loaded = store.load_task(task.task_id)
    assert loaded is not None
    assert loaded.task_class is TaskClass.RECOVERY
    json_store = JsonStateStore(tmp_path / "state.json")
    json_store.save_task(task)
    loaded_json = json_store.load_task(task.task_id)
    assert loaded_json is not None
    assert loaded_json.task_class is TaskClass.RECOVERY
    normal = Task(objective="normal persisted task")
    with SQLiteStateStore(tmp_path / "normal.sqlite3") as store:
        store.save_task(normal)
        loaded_normal = store.load_task(normal.task_id)
    assert loaded_normal is not None
    loaded_normal.task_class = TaskClass.RECOVERY
    assert loaded_normal._authority is None


def test_limits_reject_unbounded_or_invalid_values():
    with pytest.raises(ProtocolError):
        ExecutionLimits(max_steps=None)  # type: ignore[arg-type]
    with pytest.raises(ProtocolError):
        ExecutionLimits(max_cost=float("inf"))
    with pytest.raises(ProtocolError):
        Task(objective="x", depth=-1)


def test_protocol_rejects_invalid_ids_and_missing_required_text():
    with pytest.raises(ProtocolError):
        Task(task_id="not-a-uuid", objective="x")
    with pytest.raises(ProtocolError):
        Task(objective="")
    with pytest.raises(ProtocolError):
        ToolCall(tool_name="", arguments={})
