from __future__ import annotations

from scripts.devfarm_feasibility import assess_worker_feasibility


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
    source = ("value = 1\n" * 40_000).encode("utf-8")

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
