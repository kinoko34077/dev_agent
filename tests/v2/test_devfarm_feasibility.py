from __future__ import annotations

import ast
import inspect

import pytest

import scripts.devfarm_feasibility as feasibility_module
from scripts.devfarm_contracts import MAX_OUTBOUND_BYTES, WORKER_OUTPUT_MODES
from scripts.devfarm_worker_input import MAX_INPUT_FILE_BYTES
from scripts.devfarm_worker_output import (
    FULL_WORKER_OUTPUT_MODE,
    MINIMAL_WORKER_OUTPUT_MODE,
    LOCALIZED_FILE_EDIT_MODE,
    worker_output_mode,
)
from scripts.devfarm_feasibility import (
    FEASIBILITY_REASON_CODES,
    assess_worker_feasibility,
    is_feasibility_action_for_status,
    plan_block_reason,
    plan_status_for_block_reason,
)


def _manifest(*, mode: str = "minimal_file_replacement") -> dict[str, object]:
    return {
        "task_id": "feasibility-task",
        "allowed_files": ["src/example.py"],
        "outbound_files": ["src/example.py"],
        "new_files": [],
        "acceptance": ["the focused test passes"],
        "test_commands": ["python -m compileall -q src/example.py"],
        "output_contract": {"mode": mode},
    }


def test_feasibility_reports_fit_without_mutating_manifest() -> None:
    manifest = _manifest()
    before = dict(manifest)

    result = assess_worker_feasibility(
        manifest,
        {"src/example.py": b"def example():\n    return 1\n"},
    )

    assert result.status == "fits"
    assert result.input_file_count == 1
    assert result.input_bytes > 0
    assert result.output_capacity_tokens == 8192
    assert result.reasons == ()
    assert manifest == before


def test_feasibility_reframes_oversized_input_and_output_envelope() -> None:
    manifest = _manifest()
    source = ("value = 1\n" * 9_000).encode("utf-8")

    result = assess_worker_feasibility(
        manifest,
        {"src/example.py": source},
    )

    assert result.status == "reframe_required"
    assert "input_file_too_large" in result.reasons
    assert "output_envelope_too_large" in result.reasons
    assert result.recommended_action == "split_input_scope"


def test_feasibility_rejects_invalid_acceptance_and_missing_input() -> None:
    manifest = _manifest()
    manifest["acceptance"] = []
    manifest["test_commands"] = ["not-a-supported-host-command"]

    result = assess_worker_feasibility(manifest, {})

    assert result.status == "invalid_acceptance"
    assert "missing_input:src/example.py" in result.reasons
    assert "acceptance_missing" in result.reasons
    assert "test_command_invalid" in result.reasons
    assert result.recommended_action == "repair_manifest_acceptance"


def test_new_file_task_can_fit_without_source_context() -> None:
    manifest = {
        "task_id": "new-file-feasibility",
        "allowed_files": ["docs/example.md"],
        "outbound_files": [],
        "new_files": ["docs/example.md"],
        "acceptance": ["the file exists"],
        "test_commands": ["python -m compileall -q src"],
        "output_contract": {"mode": "minimal_file_replacement"},
    }

    result = assess_worker_feasibility(manifest, {})

    assert result.status == "fits"
    assert result.input_file_count == 0
    assert result.estimated_output_chars >= 256


def test_feasibility_accounts_for_read_context_and_missing_dependencies() -> None:
    manifest = _manifest()
    manifest["read_files"] = ["src/example.py", "src/missing.py"]

    result = assess_worker_feasibility(
        manifest,
        {"src/example.py": b"def example():\n    return 1\n"},
    )

    assert result.status == "invalid_acceptance"
    assert result.context_file_count == 2
    assert result.missing_context_files == ("src/missing.py",)
    assert "missing_context:src/missing.py" in result.reasons


def test_localized_output_uses_declared_change_budget_instead_of_full_file_size() -> None:
    manifest = _manifest(mode="localized_file_edit")
    manifest["output_contract"] = {
        "mode": "localized_file_edit",
        "expected_output_chars": 240,
    }
    source = ("value = 1\n" * 2_500).encode("utf-8")

    result = assess_worker_feasibility(manifest, {"src/example.py": source})

    assert result.status == "fits"
    assert result.estimated_output_chars == 240
    assert result.input_bytes == len(source)


def test_localized_output_without_change_budget_requires_reframing() -> None:
    manifest = _manifest(mode="localized_file_edit")
    source = ("value = 1\n" * 40_000).encode("utf-8")

    result = assess_worker_feasibility(manifest, {"src/example.py": source})

    assert result.status == "reframe_required"
    assert "localized_output_budget_missing" in result.reasons
    assert result.recommended_action == "declare_output_budget"


def test_feasibility_consumer_contract_is_owned_by_producer() -> None:
    # The Supervisor must not maintain another inverse status/action vocabulary.
    for status in ("reframe_required", "invalid_acceptance"):
        block_reason = plan_block_reason(status)
        assert plan_status_for_block_reason(block_reason) == status
    assert plan_status_for_block_reason("unknown") is None
    assert plan_status_for_block_reason(None) is None

    assert is_feasibility_action_for_status("reframe_required", "split_input_scope")
    assert is_feasibility_action_for_status("reframe_required", "declare_output_budget")
    assert is_feasibility_action_for_status("invalid_acceptance", "repair_manifest_acceptance")
    assert not is_feasibility_action_for_status("reframe_required", "repair_manifest_acceptance")
    assert not is_feasibility_action_for_status("invalid_acceptance", "split_input_scope")
    assert not is_feasibility_action_for_status("reframe_required", [])

    valid = assess_worker_feasibility(_manifest(), {"src/example.py": "x = 1\n"})
    assert valid.output_mode in WORKER_OUTPUT_MODES
    assert set(valid.reasons) <= FEASIBILITY_REASON_CODES
    assert "output_envelope_too_large" in FEASIBILITY_REASON_CODES

    assert WORKER_OUTPUT_MODES == frozenset({
        FULL_WORKER_OUTPUT_MODE,
        MINIMAL_WORKER_OUTPUT_MODE,
        LOCALIZED_FILE_EDIT_MODE,
    })
    for output_mode in WORKER_OUTPUT_MODES:
        assert worker_output_mode({"mode": output_mode}) == output_mode


def test_default_output_capacity_covers_every_canonical_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    assert set(feasibility_module._DEFAULT_OUTPUT_TOKENS) == WORKER_OUTPUT_MODES
    for mode in WORKER_OUTPUT_MODES:
        result = assess_worker_feasibility(_manifest(mode=mode), {"src/example.py": "x = 1\n"})
        assert result.output_capacity_tokens == feasibility_module._DEFAULT_OUTPUT_TOKENS[mode]

    # Missing future defaults must fail as a bounded feasibility contract error.
    monkeypatch.delitem(feasibility_module._DEFAULT_OUTPUT_TOKENS, "full_result")
    with pytest.raises(feasibility_module.FeasibilityError, match="default token capacity"):
        assess_worker_feasibility(_manifest(mode="full_result"), {"src/example.py": "x = 1\n"})

def test_feasibility_static_reason_vocabulary_matches_producer_emissions() -> None:
    # The producer owns static reason semantics. Reject newly emitted literals
    # that were not also added to the Supervisor-facing canonical vocabulary.
    source = ast.parse(inspect.getsource(assess_worker_feasibility))
    emitted = {
        node.args[0].value
        for node in ast.walk(source)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "reasons"
        and node.func.attr == "append"
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert emitted == FEASIBILITY_REASON_CODES


def test_every_declared_static_feasibility_reason_is_actually_reachable() -> None:
    basic = {"src/example.py": b"x = 1\n"}
    many_paths = [f"src/file_{index}.py" for index in range(9)]
    many_files = {path: b"x" * MAX_INPUT_FILE_BYTES for path in many_paths}
    cases = [
        ("acceptance_missing", {"acceptance": []}, basic, {}),
        ("test_commands_missing", {"test_commands": []}, basic, {}),
        ("test_command_invalid", {"test_commands": ["not-a-supported-host-command"]}, basic, {}),
        ("output_scope_invalid", {"outbound_files": []}, {}, {}),
        ("new_file_overlaps_input", {"new_files": ["src/example.py"]}, basic, {}),
        ("input_not_utf8", {}, {"src/example.py": b"\xff"}, {}),
        ("input_contains_nul", {}, {"src/example.py": b"\x00"}, {}),
        ("input_file_too_large", {}, {"src/example.py": b"x" * (MAX_INPUT_FILE_BYTES + 1)}, {}),
        (
            "input_scope_too_large",
            {"allowed_files": many_paths, "outbound_files": many_paths},
            many_files,
            {},
        ),
        (
            "context_scope_too_large",
            {"read_files": ["src/context.py"]},
            {**basic, "src/context.py": b"x" * (MAX_OUTBOUND_BYTES + 1)},
            {},
        ),
        ("localized_output_budget_missing", {"output_contract": {"mode": "localized_file_edit"}}, basic, {}),
        (
            "output_envelope_too_large",
            {"output_contract": {"mode": "minimal_file_replacement", "expected_output_chars": 4096}},
            basic,
            {"max_output_tokens": 256},
        ),
    ]
    observed: set[str] = set()
    for expected, changes, files, kwargs in cases:
        manifest = _manifest()
        manifest.update(changes)
        result = assess_worker_feasibility(manifest, files, **kwargs)
        assert expected in result.reasons
        observed.update(reason for reason in result.reasons if ":" not in reason)

    assert observed == FEASIBILITY_REASON_CODES

    # Path-specific reasons are deliberately not enumerable static codes;
    # the Supervisor projects these two bounded prefixes independently.
    assert "missing_input:src/example.py" in assess_worker_feasibility(_manifest(), {}).reasons
    manifest = _manifest()
    manifest["read_files"] = ["src/missing.py"]
    assert "missing_context:src/missing.py" in assess_worker_feasibility(manifest, basic).reasons
