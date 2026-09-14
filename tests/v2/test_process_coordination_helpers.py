from __future__ import annotations

import math

import pytest

from src.dev_agent.coordination.protocol_helpers import (
    CoordinationValidationError,
    ensure_json_safe,
    ensure_secret_free,
    validate_identifier,
    validate_relative_path,
    validate_string_sequence,
    validate_timestamp,
)


def test_helpers_validate_bounded_identifiers_paths_and_timestamps() -> None:
    assert validate_identifier("agent-1", "role") == "agent-1"
    assert validate_relative_path("notes/checkpoint.json", "path") == "notes/checkpoint.json"
    assert validate_timestamp("2026-09-14T12:00:00+00:00", "created_at")
    assert validate_string_sequence(["one", "two"], "capabilities") == ("one", "two")


@pytest.mark.parametrize(
    ("validator", "value"),
    [
        (validate_identifier, "../outside"),
        (validate_relative_path, "../outside.json"),
        (validate_timestamp, "not-a-timestamp"),
    ],
)
def test_helpers_reject_invalid_scalars(validator, value) -> None:
    with pytest.raises(CoordinationValidationError):
        validator(value, "value")


def test_helpers_reject_nan_and_secret_shaped_values() -> None:
    with pytest.raises(CoordinationValidationError):
        ensure_json_safe({"value": math.nan}, "payload")
    with pytest.raises(CoordinationValidationError):
        ensure_secret_free({"api_key": "should-not-be-stored"}, "payload")

