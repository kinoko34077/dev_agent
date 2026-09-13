from __future__ import annotations

import json
from dev_agent.backends.codex_jsonl import parse_codex_jsonl


def test_codex_jsonl_usage_accumulation_and_filtering() -> None:
    records = [
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "reasoning_output_tokens": 10,
                "unsupported_custom_field": 999,
            },
        },
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 200,
                "output_tokens": 150,
                "reasoning_output_tokens": 20,
                "unsupported_custom_field": 111,
            },
        },
    ]

    jsonl_text = "\n".join(json.dumps(r) for r in records)
    result = parse_codex_jsonl(jsonl_text)

    assert result.usage == {
        "input_tokens": 300,
        "output_tokens": 200,
        "reasoning_output_tokens": 30,
    }
    assert "unsupported_custom_field" not in result.usage
