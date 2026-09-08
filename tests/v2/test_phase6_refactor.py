from src.dev_agent.domain.protocol import Task
from src.dev_agent.runtime import RuntimeState


def test_runtime_state_round_trips_checkpoint_json_without_aliasing():
    state = RuntimeState.initial(Task(objective="typed state"), now=100.0)
    state["next_step_order"] = 2
    state["pending_tool_calls"].append({"call_id": "call"})

    checkpoint = state.to_checkpoint()
    checkpoint["pending_tool_calls"].append({"call_id": "external"})
    restored = RuntimeState.from_checkpoint(state.to_checkpoint())

    assert restored.deadline_epoch == 100.0 + 300.0
    assert restored["next_step_order"] == 2
    assert restored.pending_tool_calls == [{"call_id": "call"}]
    assert state.pending_tool_calls == [{"call_id": "call"}]
    assert restored.active_request_id is None
