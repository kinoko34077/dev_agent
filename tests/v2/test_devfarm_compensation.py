from __future__ import annotations

import pytest

from scripts.devfarm_compensation import select_worker_compensation
from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_contracts import WORKER_OUTPUT_MODES
from scripts.devfarm_worker import _rework_compensation


def _manifest(*, mode: str = "minimal_file_replacement", new_files: list[str] | None = None) -> dict[str, object]:
    return {
        "output_contract": {"mode": mode},
        "outbound_files": ["src/example.py"],
        "new_files": list(new_files or []),
    }


def test_format_failure_on_existing_source_switches_to_host_localized_edit() -> None:
    decision = select_worker_compensation(
        _manifest(),
        failure_class="FORMAT_PATCH",
        failure_code="WORKER_OUTPUT_INVALID_JSON",
        stage="worker_output_validation",
    )

    assert decision.action == "switch_output_mode"
    assert decision.output_mode == "localized_file_edit"
    assert decision.expected_output_chars == 4096
    assert decision.authority == "host_policy"
    assert "exact_line_anchor" in decision.context_hints


def test_format_failure_on_new_file_keeps_minimal_contract() -> None:
    decision = select_worker_compensation(
        _manifest(new_files=["src/example.py"]),
        failure_class="FORMAT_PATCH",
        failure_code="WORKER_OUTPUT_LINE_SHAPE",
        stage="worker_output_validation",
    )

    assert decision.action == "repair_same_task"
    assert decision.output_mode == "minimal_file_replacement"
    assert decision.reason_code == "format_new_file_contract"


def test_truncated_new_file_output_requires_bounded_reframe() -> None:
    decision = select_worker_compensation(
        _manifest(new_files=["src/example.py"]),
        failure_class="FORMAT_PATCH",
        failure_code="WORKER_OUTPUT_TRUNCATED",
        stage="worker_output_validation",
    )

    assert decision.action == "reframe_task"
    assert decision.output_mode is None
    assert decision.reframe_required is True
    assert decision.reason_code == "truncated_new_file_requires_reframe"
    assert "split_independent_region" in decision.context_hints


def test_syntax_failure_can_switch_existing_source_to_localized_edit() -> None:
    decision = select_worker_compensation(
        _manifest(mode="full_result"),
        failure_class="SEMANTIC_TEST",
        failure_code="WORKER_HOST_VERIFICATION_SYNTAX",
        stage="host_verification",
    )

    assert decision.action == "switch_output_mode"
    assert decision.output_mode == "localized_file_edit"
    assert decision.expected_output_chars == 4096
    assert "host_failure_category" in decision.context_hints


def test_semantic_failure_adds_bounded_context_without_rewriting_contract() -> None:
    decision = select_worker_compensation(
        _manifest(mode="full_result"),
        failure_class="SEMANTIC_TEST",
        failure_code="WORKER_HOST_VERIFICATION_FAILED",
        stage="host_verification",
    )

    assert decision.action == "add_minimal_context"
    assert decision.output_mode == "full_result"
    assert "independent_test_required" in decision.context_hints


@pytest.mark.parametrize("failure_class", ["UNKNOWN_EXTERNAL_EFFECT", "PROVIDER_TRANSPORT", "SECURITY_EGRESS_AUTHORITY"])
def test_external_or_authority_failure_never_selects_model_repair(failure_class: str) -> None:
    decision = select_worker_compensation(
        _manifest(),
        failure_class=failure_class,
        failure_code="BOUNDARY_FAILURE",
        stage="host_boundary",
    )

    assert decision.action == "stop_model_repair"
    assert decision.output_mode is None
    assert decision.context_hints == ()


def test_unknown_failure_class_fails_closed() -> None:
    with pytest.raises(DevFarmError, match="failure_class"):
        select_worker_compensation(
            _manifest(),
            failure_class="invented_failure",
            failure_code="X",
            stage="worker_output_validation",
        )


def test_compensation_rejects_unbounded_output_budget() -> None:
    with pytest.raises(DevFarmError, match="expected_output_chars"):
        from scripts.devfarm_compensation import WorkerCompensation

        WorkerCompensation(
            action="switch_output_mode",
            reason_code="test",
            output_mode="localized_file_edit",
            expected_output_chars="unbounded",  # type: ignore[arg-type]
        )

def test_worker_rework_compensation_accepts_every_canonical_output_mode() -> None:
    for mode in WORKER_OUTPUT_MODES:
        decision = _rework_compensation({
            "rework_handoff": {
                "repair_context": {
                    "compensation": {
                        "authority": "host_policy",
                        "action": "switch_output_mode",
                        "prompt_contract": "host-localized-edit",
                        "output_mode": mode,
                    },
                },
            },
        })
        assert decision is not None
        assert decision["output_mode"] == mode


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("action", []),
        ("action", {"x": "y"}),
        ("prompt_contract", ["host-localized-edit"]),
        ("output_mode", ["localized_file_edit"]),
        ("output_mode", {"mode": "localized_file_edit"}),
        ("output_mode", 123),
    ],
)
def test_worker_rework_compensation_rejects_malformed_route_types(
    field: str, invalid: object
) -> None:
    compensation: dict[str, object] = {
        "authority": "host_policy",
        "action": "switch_output_mode",
        "prompt_contract": "host-localized-edit",
        "output_mode": "localized_file_edit",
    }
    compensation[field] = invalid
    with pytest.raises(DevFarmError, match="unsupported"):
        _rework_compensation({"rework_handoff": {"repair_context": {"compensation": compensation}}})
