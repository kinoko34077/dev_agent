from __future__ import annotations

import json

import pytest

from scripts.devfarm import DevFarmError, validate_manifest
from scripts.devfarm_worker import run_worker
from scripts.devfarm_worker_output import (
    MINIMAL_WORKER_OUTPUT_KEYS,
    minimal_worker_output_schema,
    worker_output_mode,
)
from tests.v2.devfarm_test_support import _WorkerProvider, _workspace


def test_worker_output_mode_is_manifest_selectable_and_fail_closed() -> None:
    assert worker_output_mode({"format": "json"}) == "full_result"
    assert worker_output_mode({"mode": "minimal_file_replacement"}) == "minimal_file_replacement"
    assert worker_output_mode({}, local_ollama=True) == "minimal_file_replacement"

    try:
        worker_output_mode({"mode": "invented_mode"})
    except ValueError as exc:
        assert "output_contract.mode" in str(exc)
    else:  # pragma: no cover - the assertion documents the fail-closed contract
        raise AssertionError("unsupported output mode must be rejected")


def test_minimal_worker_schema_binds_file_replacement_keys_to_host_paths() -> None:
    schema = minimal_worker_output_schema(
        allowed_paths=("src/example.py",),
        line_array_only=True,
    )

    replacement = schema["properties"]["file_replacements"]
    assert replacement["additionalProperties"] is False
    assert set(replacement["properties"]) == {"src/example.py"}
    assert replacement["properties"]["src/example.py"]["type"] == "array"
    assert "oneOf" not in replacement["properties"]["src/example.py"]
    assert set(schema["properties"]) == {"file_replacements"}
    assert MINIMAL_WORKER_OUTPUT_KEYS == frozenset({"file_replacements"})

    compatible_schema = minimal_worker_output_schema(allowed_paths=("src/example.py",))
    assert "oneOf" in compatible_schema["properties"]["file_replacements"]["properties"]["src/example.py"]


def test_manifest_rejects_unknown_worker_output_mode(tmp_path) -> None:
    _root, manifest_path = _workspace(tmp_path, prepare=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_contract"] = {"mode": "invented_mode"}

    with pytest.raises(DevFarmError, match="output_contract.mode"):
        validate_manifest(manifest)


def test_remote_worker_can_use_explicit_minimal_change_proposal(tmp_path) -> None:
    root, manifest_path = _workspace(tmp_path, prepare=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_contract"] = {
        "format": "json",
        "mode": "minimal_file_replacement",
    }
    manifest["approved_provider_ids"] = ["cloudflare"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    provider = _WorkerProvider(
        {
            "file_replacements": {
                "tests/v2/test_target.py": [
                    "def test_target():",
                    "    assert True",
                    "    return None",
                    "",
                ]
            }
        }
    )

    result = run_worker(root, manifest_path, provider=provider)

    assert result["status"] == "completed"
    assert result["changed_files"] == ["tests/v2/test_target.py"]
    assert result["worker_metrics"]["output_contract_mode"] == "minimal_file_replacement"
    assert result["worker_metrics"]["host_generated_patch"] == "file_replacements"
    assert result["worker_metrics"]["canonicalization"]["rules"] == [
        "line_array_terminal_empty_lines"
    ]


def test_minimal_worker_contract_rejects_text_replacement_and_emits_line_array_repair(tmp_path) -> None:
    root, manifest_path = _workspace(tmp_path, prepare=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_contract"] = {"format": "json", "mode": "minimal_file_replacement"}
    manifest["approved_provider_ids"] = ["cloudflare"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    provider = _WorkerProvider(
        {
            "file_replacements": {
                "tests/v2/test_target.py": "def test_target():\n    assert True\n    return None\n"
            }
        }
    )

    result = run_worker(root, manifest_path, provider=provider)

    assert result["status"] == "failed"
    assert result["worker_metrics"]["proposal_preflight"]["error_code"] == "WORKER_OUTPUT_SCHEMA"
    assert result["failure_spec"]["location"] == "file_replacements"
    assert "array of complete source-line strings" in result["repair_directive"]["required_action"]


def test_minimal_worker_contract_rejects_embedded_newline_in_line_array_item(tmp_path) -> None:
    root, manifest_path = _workspace(tmp_path, prepare=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_contract"] = {
        "format": "json",
        "mode": "minimal_file_replacement",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    provider = _WorkerProvider(
        {
            "file_replacements": {
                "tests/v2/test_target.py": [
                    "def test_target():\n    assert True",
                    "    return None",
                ]
            }
        }
    )

    result = run_worker(root, manifest_path, provider=provider)

    assert result["status"] == "failed"
    assert result["worker_metrics"]["proposal_preflight"]["error_code"] == "WORKER_OUTPUT_LINE_SHAPE"
    assert result["failure_spec"]["expected"] == "one source line per array element"
    assert result["failure_spec"]["acceptance_checks"] == [
        "each replacement line contains no LF or CR"
    ]
    assert "split multiline content" in result["repair_directive"]["required_action"]


@pytest.mark.parametrize("extra_field, extra_value", [("status", "completed"), ("notes", "optional commentary")])
def test_minimal_proposal_contract_rejects_extra_fields_without_fallback(
    tmp_path, extra_field: str, extra_value: str
) -> None:
    root, manifest_path = _workspace(tmp_path, prepare=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_contract"] = {
        "format": "json",
        "mode": "minimal_file_replacement",
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    provider = _WorkerProvider(
        {
            "file_replacements": {"tests/v2/test_target.py": "def test_target():\n    assert True\n"},
            extra_field: extra_value,
        }
    )

    result = run_worker(root, manifest_path, provider=provider)

    assert result["status"] == "failed"
    preflight = result["worker_metrics"]["proposal_preflight"]
    assert preflight["status"] == "rejected"
    assert preflight["error_code"] == "WORKER_OUTPUT_ADDITIONAL_FIELD"
    assert preflight["fallback_eligible"] is False
    assert preflight["fallback_disposition"] == "defer_to_bounded_correction"
    assert result["failure_spec"]["expected"] == "only file_replacements"
    assert "exactly one JSON object with file_replacements only" in result["repair_directive"]["required_action"]
    assert "optional notes" not in result["repair_directive"]["required_action"]
