from __future__ import annotations

import json

import pytest

from src.dev_agent.backends.codex_jsonl import CodexJsonlParseError, parse_codex_jsonl


def _line(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False)


def test_parse_codex_jsonl_keeps_structural_events_and_usage_only():
    output = "\n".join(
        [
            _line({"type": "thread.started", "thread_id": "thread-123"}),
            _line({"type": "turn.started"}),
            _line(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item-1",
                        "type": "command_execution",
                        "command": "type secret.txt",
                        "aggregated_output": "Bearer should-not-be-retained",
                        "exit_code": 0,
                    },
                }
            ),
            _line(
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 4,
                        "output_tokens": 3,
                        "reasoning_output_tokens": 1,
                    },
                }
            ),
        ]
    )

    parsed = parse_codex_jsonl(output)

    assert parsed.thread_id == "thread-123"
    assert [event.event_type for event in parsed.events] == [
        "thread.started",
        "turn.started",
        "item.completed",
        "turn.completed",
    ]
    assert parsed.events[0].sequence == 1
    assert parsed.events[2].payload == {"item_id": "item-1", "item_type": "command_execution", "exit_code": 0}
    assert parsed.usage == {
        "input_tokens": 10,
        "cached_input_tokens": 4,
        "output_tokens": 3,
        "reasoning_output_tokens": 1,
    }
    serialized = json.dumps([event.payload for event in parsed.events])
    assert "secret.txt" not in serialized
    assert "Bearer" not in serialized


def test_parse_codex_jsonl_rejects_malformed_or_oversized_records():
    with pytest.raises(CodexJsonlParseError, match="valid JSON object"):
        parse_codex_jsonl("not-json")

    with pytest.raises(CodexJsonlParseError, match="too many"):
        parse_codex_jsonl("\n".join(_line({"type": "turn.started"}) for _ in range(3)), max_records=2)


@pytest.mark.parametrize(
    "usage",
    [
        {"input_tokens": -1},
        {"output_tokens": 1.5},
        {"input_tokens": "10"},
    ],
)
def test_parse_codex_jsonl_rejects_invalid_usage_scalars(usage):
    output = _line({"type": "turn.completed", "usage": usage})

    with pytest.raises(CodexJsonlParseError, match="usage"):
        parse_codex_jsonl(output)


def test_parse_codex_jsonl_rejects_conflicting_thread_ids():
    output = "\n".join(
        [
            _line({"type": "thread.started", "thread_id": "thread-a"}),
            _line({"type": "thread.started", "thread_id": "thread-b"}),
        ]
    )

    with pytest.raises(CodexJsonlParseError, match="thread_id"):
        parse_codex_jsonl(output)


def test_parse_codex_jsonl_rejects_free_form_structural_tokens():
    with pytest.raises(CodexJsonlParseError, match="record.type"):
        parse_codex_jsonl(_line({"type": "Bearer secret-value"}))
