from __future__ import annotations

import pytest

from scripts.devfarm import DevFarmError
from scripts.devfarm_worker_output import (
    build_worker_metrics,
    extract_json_object,
    normalize_model_status,
    safe_host_failure_metadata,
    safe_usage,
)
from src.dev_agent.domain.protocol import ModelRequest


def test_extract_json_object_accepts_one_fenced_object_only() -> None:
    assert extract_json_object("```json\n{\"status\": \"completed\"}\n```") == {
        "status": "completed"
    }
    with pytest.raises(DevFarmError, match="JSON object"):
        extract_json_object("[1, 2]")


def test_output_normalization_is_bounded_and_provider_neutral() -> None:
    assert normalize_model_status("ok") == "completed"
    with pytest.raises(DevFarmError, match="non-empty"):
        normalize_model_status("")
    assert safe_usage(
        {
            "input_tokens": 12,
            "private_raw": "must not persist",
            "quota_observation": {"remaining": 4, "secret": "omit"},
        }
    ) == {"input_tokens": 12, "quota_observation": {"remaining": 4}}


def test_build_worker_metrics_keeps_bounded_identity_and_usage() -> None:
    request = ModelRequest(task_id="00000000-0000-0000-0000-000000000001", messages=[])

    class Provider:
        provider_id = "gemini"
        provider_binding_id = "gemini:worker:free-3"
        model = "gemini-3.6-flash"
        intelligence_tier = "L1"

    class Response:
        provider = "gemini"
        model = "gemini-3.6-flash"
        usage = {"input_tokens": 5, "output_tokens": 7}

    metrics = build_worker_metrics(
        Provider(),
        request,
        response=Response(),
        elapsed_ms=12,
        task_type="parser",
    )

    assert metrics["provider_id"] == "gemini"
    assert metrics["provider_binding_id"] == "gemini:worker:free-3"
    assert metrics["usage"] == {"input_tokens": 5, "output_tokens": 7}


def test_safe_host_failure_metadata_drops_untrusted_child_values() -> None:
    class Error(Exception):
        host_failure_category = "secret-category"
        host_failure_type = "RuntimeError\nraw child output"

    assert safe_host_failure_metadata(Error()) == {}


def test_safe_host_failure_metadata_keeps_transport_projection_without_message() -> None:
    class Error(Exception):
        host_failure_category = "transport"
        host_failure_type = "RuntimeError"
        transport_failure_category = "connection_reset"
        transport_stage = "response_wait"
        transport_exception_type = "ConnectionResetError"
        transport_errno = 104
        transport_winerror = 10054

    metadata = safe_host_failure_metadata(Error("raw transport detail must not cross"))

    assert metadata == {
        "host_failure_category": "transport",
        "host_failure_type": "RuntimeError",
        "transport_failure_category": "connection_reset",
        "transport_stage": "response_wait",
        "transport_exception_type": "ConnectionResetError",
        "transport_errno": 104,
        "transport_winerror": 10054,
    }
    assert "raw transport detail" not in str(metadata)
