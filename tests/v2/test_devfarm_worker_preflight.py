from __future__ import annotations

import json

import pytest

from scripts.devfarm import DevFarmError, validate_manifest
from scripts.devfarm_worker import run_worker
from scripts.devfarm_worker_output import worker_output_mode
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


def test_minimal_proposal_contract_rejects_extra_fields_without_fallback(tmp_path) -> None:
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
            "status": "completed",
        }
    )

    result = run_worker(root, manifest_path, provider=provider)

    assert result["status"] == "failed"
    preflight = result["worker_metrics"]["proposal_preflight"]
    assert preflight["status"] == "rejected"
    assert preflight["error_code"] == "WORKER_OUTPUT_ADDITIONAL_FIELD"
    assert preflight["fallback_eligible"] is False
    assert preflight["fallback_disposition"] == "defer_to_bounded_correction"
