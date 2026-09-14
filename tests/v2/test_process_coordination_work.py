from __future__ import annotations

import json

import pytest

from src.dev_agent.coordination.work import (
    InterruptFrame,
    InterruptStack,
    InterruptionMode,
    ResumeCapsule,
    WorkAddress,
    classify_intervention,
)


def test_work_address_round_trips_mixed_sequential_and_parallel_segments() -> None:
    address = WorkAddress.parse("5-B-8-3")

    assert str(address) == "5-B-8-3"
    assert address.to_dict() == {"address": "5-B-8-3"}
    assert WorkAddress.from_dict(address.to_dict()) == address
    assert str(address.parent) == "5-B-8"
    assert WorkAddress.parse("5").parent is None


@pytest.mark.parametrize("value", ["", "5-", "5-b", "0", "5-0", "5-A1", "../5", "5--B"])
def test_work_address_rejects_unsafe_segments(value: str) -> None:
    with pytest.raises(ValueError):
        WorkAddress.parse(value)


def test_next_child_allocates_numeric_and_parallel_lane_without_collision() -> None:
    parent = WorkAddress.parse("5-B-8")
    existing = ["5-B-8-1", "5-B-8-2", "5-B-8-A", "5-B-8-C", "5-B-8-2-1"]

    assert str(parent.next_child(existing, kind="numeric")) == "5-B-8-3"
    assert str(parent.next_child(existing, kind="letter")) == "5-B-8-B"


def test_resume_capsule_is_bounded_and_json_serializable() -> None:
    capsule = ResumeCapsule(
        work_address=WorkAddress.parse("5-B-8"),
        status="RUNNING",
        objective="coordinate the mailbox boundary",
        current_action="checking generation fencing",
        completed=("peer identity",),
        next_action="add the mailbox focused test",
        resume_from="after the generation-fencing test",
        blocked_by=(),
        owned_paths=("src/dev_agent/coordination/work.py",),
        checkpoint_revision="rev-a",
    )

    restored = ResumeCapsule.from_dict(capsule.to_dict())
    assert restored == capsule
    assert capsule.to_dict()["work_address"] == "5-B-8"
    json.dumps(restored.to_dict(), ensure_ascii=False, allow_nan=False)

    with pytest.raises(ValueError):
        ResumeCapsule(
            work_address=WorkAddress.parse("5-B-8"),
            status="RUNNING",
            objective="work",
            current_action="work",
            completed=(),
            next_action="continue",
            resume_from="now",
            blocked_by=(),
            owned_paths=("../outside.py",),
            checkpoint_revision="rev-a",
        )

    with pytest.raises(ValueError):
        ResumeCapsule.from_dict(
            {
                **capsule.to_dict(),
                "objective": 123,
            }
        )


def test_interrupt_stack_returns_latest_frame_first_and_is_bounded() -> None:
    first = InterruptFrame(
        task_id="task-1",
        work_address=WorkAddress.parse("5-B"),
        resume_from="step 2",
        next_action="finish the parent task",
        checkpoint_revision="rev-a",
    )
    second = InterruptFrame(
        task_id="task-2",
        work_address=WorkAddress.parse("5-B-1"),
        resume_from="step 1",
        next_action="return to 5-B",
        checkpoint_revision="rev-a",
    )

    stack = InterruptStack(max_depth=2).push(first).push(second)
    top, remaining = stack.pop()
    assert top == second
    assert remaining.pop()[0] == first

    with pytest.raises(ValueError):
        stack.push(
            InterruptFrame(
                task_id="task-3",
                work_address=WorkAddress.parse("5-B-1-1"),
                resume_from="step 1",
                next_action="return",
                checkpoint_revision="rev-a",
            )
        )

    assert InterruptStack().max_depth == 8
    assert InterruptStack.from_dict(stack.to_dict()) == stack


def test_interrupt_frame_rejects_invalid_task_or_checkpoint_values() -> None:
    with pytest.raises(ValueError):
        InterruptFrame(
            task_id="",
            work_address=WorkAddress.parse("5-B"),
            resume_from="step 2",
            next_action="finish",
            checkpoint_revision="rev-a",
        )

    with pytest.raises(ValueError):
        InterruptStack.from_dict({"max_depth": 0, "frames": []})


def test_ambiguous_intervention_defaults_to_non_interrupting_note() -> None:
    assert classify_intervention(None) is InterruptionMode.NOTE
    assert classify_intervention("unknown") is InterruptionMode.NOTE
    assert classify_intervention("PARALLEL") is InterruptionMode.PARALLEL
    assert classify_intervention(InterruptionMode.INTERRUPT) is InterruptionMode.INTERRUPT
    assert classify_intervention("CANCEL") is InterruptionMode.CANCEL
