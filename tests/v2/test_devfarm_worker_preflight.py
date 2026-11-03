from __future__ import annotations

import json

import pytest

from scripts.devfarm import DevFarmError, validate_manifest
from scripts.devfarm_contracts import WORKER_OUTPUT_MODES, canonical_digest
from scripts.devfarm_feasibility import FeasibilityError, assess_worker_feasibility
from scripts.devfarm_worker import run_worker
from scripts.devfarm_worker_output import (
    MINIMAL_WORKER_OUTPUT_KEYS,
    minimal_worker_output_schema,
    worker_output_mode,
)
from src.dev_agent.domain.protocol import ModelResponse
from tests.v2.devfarm_test_support import _WorkerProvider, _workspace

pytestmark = pytest.mark.synthetic_no_charge_worker_billing


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


def test_manifest_tracks_explicit_new_worker_output_paths_without_sending_them(tmp_path) -> None:
    _root, manifest_path = _workspace(tmp_path, prepare=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["allowed_files"] = ["tests/v2/test_target.py", "tests/v2/generated.py"]
    manifest["read_files"] = ["tests/v2/test_target.py"]
    manifest["outbound_files"] = ["tests/v2/test_target.py"]
    manifest["new_files"] = ["tests/v2/generated.py"]

    normalized = validate_manifest(manifest)

    assert normalized["new_files"] == ["tests/v2/generated.py"]
    assert normalized["outbound_files"] == ["tests/v2/test_target.py"]

    manifest["new_files"] = ["tests/v2/test_target.py"]
    with pytest.raises(DevFarmError, match="new_files.*outbound_files"):
        validate_manifest(manifest)


def test_minimal_worker_can_materialize_explicit_new_file_without_outbound_source(tmp_path) -> None:
    root, manifest_path = _workspace(tmp_path, prepare=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["allowed_files"] = ["tests/v2/test_target.py", "tests/v2/generated.py"]
    manifest["read_files"] = ["tests/v2/test_target.py"]
    manifest["outbound_files"] = ["tests/v2/test_target.py"]
    manifest["new_files"] = ["tests/v2/generated.py"]
    manifest["output_contract"] = {"format": "json", "mode": "minimal_file_replacement"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    provider = _WorkerProvider(
        {
            "file_replacements": {
                "tests/v2/generated.py": [
                    "def test_generated():",
                    "    assert True",
                ]
            }
        }
    )

    result = run_worker(root, manifest_path, provider=provider)

    assert result["status"] == "completed"
    assert result["changed_files"] == ["tests/v2/generated.py"]
    assert result["worker_metrics"]["host_generated_patch"] == "file_replacements"
    assert result["worker_metrics"]["host_generated_patch_paths"] == ["tests/v2/generated.py"]


def test_worker_feasibility_blocks_oversized_input_before_provider_request(tmp_path) -> None:
    root, manifest_path = _workspace(tmp_path, prepare=False)
    target = root / "tests/v2/test_target.py"
    target.write_text("value = 1\n" * 40_000, encoding="utf-8")
    from tests.v2.devfarm_test_support import _git

    _git("add", "tests/v2/test_target.py", cwd=root)
    _git("commit", "-m", "oversized worker input", cwd=root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["base_revision"] = _git("rev-parse", "HEAD", cwd=root).stdout.strip()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    provider = _WorkerProvider({"file_replacements": {"tests/v2/test_target.py": ["value = 2"]}})

    result = run_worker(root, manifest_path, provider=provider)

    assert result["status"] == "failed"
    assert provider.request_count == 0
    assert result["worker_metrics"]["pre_dispatch_block"] == "feasibility"
    assert result["worker_metrics"]["feasibility"]["status"] == "reframe_required"
    assert "input_file_too_large" in result["worker_metrics"]["feasibility"]["reasons"]
    assert result["worker_metrics"]["manifest_sha256"] == canonical_digest(validate_manifest(manifest))


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


def test_length_terminated_json_failure_records_bounded_truncation_repair(tmp_path) -> None:
    root, manifest_path = _workspace(tmp_path, prepare=False)

    class TruncatedWorker(_WorkerProvider):
        def request(self, request):
            self.request_count += 1
            return ModelResponse(
                provider=self.provider_id,
                model=self.model_id,
                text_segments=['{"status":"completed","patch":"'],
                finish_reason="length",
            )

    result = run_worker(root, manifest_path, provider=TruncatedWorker({}))

    assert result["status"] == "failed"
    assert result["worker_metrics"]["finish_reason"] == "length"
    assert result["worker_metrics"]["proposal_preflight"]["error_code"] == "WORKER_OUTPUT_TRUNCATED"
    assert result["known_issues"] == [
        "worker response JSON was truncated by provider output limit before object completion"
    ]
    assert "shorter, complete response" in result["repair_directive"]["required_action"]


def test_minimal_worker_contract_normalizes_embedded_newline_in_line_array_item(tmp_path) -> None:
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

    assert result["status"] == "completed"
    assert result["changed_files"] == ["tests/v2/test_target.py"]
    assert result["worker_metrics"]["host_generated_patch"] == "file_replacements"
    assert result["worker_metrics"]["canonicalization"]["rules"] == [
        "line_array_embedded_newline_split"
    ]


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

@pytest.mark.parametrize(
    "invalid",
    [
        "unknown_future_mode",
        ["localized_file_edit"],
        {"mode": "full_result"},
        123,
        True,
    ],
)
def test_worker_output_mode_boundaries_reject_untrusted_types_without_typeerror(
    tmp_path, invalid: object
) -> None:
    # Every Host boundary rejects malformed output-mode values with its
    # declared contract error, not a leaked TypeError from frozenset lookup.
    output_contract = {"mode": invalid}
    with pytest.raises(DevFarmError, match="output_contract.mode") as parser_error:
        worker_output_mode(output_contract)
    expected_names = ", ".join(sorted(WORKER_OUTPUT_MODES))
    assert expected_names in str(parser_error.value)

    _root, manifest_path = _workspace(tmp_path, prepare=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_contract"] = output_contract
    with pytest.raises(DevFarmError, match="output_contract.mode") as manifest_error:
        validate_manifest(manifest)
    assert expected_names in str(manifest_error.value)

    feasibility_manifest = {
        "task_id": "mode-boundary-test",
        "allowed_files": ["src/example.py"],
        "outbound_files": ["src/example.py"],
        "new_files": [],
        "acceptance": ["bounded test"],
        "test_commands": ["python -m compileall -q src/example.py"],
        "output_contract": output_contract,
    }
    with pytest.raises(FeasibilityError, match="output_contract.mode"):
        assess_worker_feasibility(feasibility_manifest, {"src/example.py": "x = 1\n"})
