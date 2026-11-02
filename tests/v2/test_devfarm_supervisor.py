from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest

from scripts.devfarm import write_manifest
from scripts.devfarm_commander import validate_plan
from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_supervisor_protocol import (
    advance_heartbeat,
    normalize_supervisor_metadata,
    normalize_review_decision,
    normalize_review_packet,
    record_wake,
    select_heartbeat_cadence,
)
from scripts.devfarm_supervisor import CodexSupervisedCommanderRun, main as supervisor_main
from scripts.devfarm_supervisor import _canonical_supervisor_action, _providers_for_resume
from scripts.devfarm_commander import create_plan
from scripts.devfarm_plan_queries import result_reference
from scripts.devfarm_contracts import canonical_digest
from src.dev_agent.domain.execution import CanonicalExecutionBinding, ExecutionLifecycleStage


def _plan(**overrides):
    value = {
        "run_id": "supervisor-test-001",
        "objective": "test supervisor metadata",
        "base_revision": "abc123",
        "tasks": [
            {
                "task_id": "task-001",
                "owner": "codex",
                "ownership": [],
                "dependencies": [],
                "assignment": {"owner": "codex"},
            }
        ],
        "ownership": [{"task_id": "task-001", "paths": []}],
        "assignments": [{"task_id": "task-001", "owner": "codex"}],
        "dependencies": [{"task_id": "task-001", "depends_on": []}],
        "results": [],
    }
    value.update(overrides)
    return value


def test_commander_plan_preserves_bounded_supervisor_metadata():
    normalized = validate_plan(
        _plan(
            supervisor={
                "status": "WAITING_FOR_WORKER",
                "roadmap_reference": {"path": "docs/V2_EXECUTION_PLAN.md"},
                "roadmap_position": "handoff",
                "cadence_minutes": 5,
                "unchanged_check_limit": 3,
                "next_action": "wait_for_worker",
            }
        )
    )

    assert normalized["supervisor"]["status"] == "WAITING_FOR_WORKER"
    assert normalized["supervisor"]["cadence_minutes"] == 5
    assert normalized["supervisor"]["unchanged_check_limit"] == 3
    assert normalized["supervisor"]["metrics"]["codex_wake_count"] == 0
    assert normalized["supervisor"]["review_packets"] == []


@pytest.mark.parametrize(
    ("remaining", "expected"),
    [(300, 1), (301, 5), (1200, 5), (1201, 10), (3600, 10), (3601, 15), (None, 15)],
)
def test_heartbeat_cadence_uses_bounded_time_bands(remaining, expected):
    assert select_heartbeat_cadence(remaining) == expected


def test_wake_event_is_deduplicated_without_raw_worker_output():
    metadata = normalize_supervisor_metadata()
    updated = record_wake(
        metadata,
        kind="HOST_VERIFIED_RESULT_READY",
        task_id="task-001",
        attempt_id="attempt-001",
        digest="a" * 64,
    )
    repeated = record_wake(
        updated,
        kind="HOST_VERIFIED_RESULT_READY",
        task_id="task-001",
        attempt_id="attempt-001",
        digest="a" * 64,
    )

    assert len(updated["wake_events"]) == 1
    assert repeated == updated
    assert "raw_output" not in updated["wake_events"][0]


@pytest.mark.parametrize(
    ("attempt_count", "expected_kind"),
    [(1, "WORKER_REJECTED"), (2, "WORKER_REJECTED_AFTER_RETRY")],
)
def test_supervisor_marks_rejection_with_accurate_attempt_context(
    tmp_path, attempt_count, expected_kind
):
    write_manifest(
        tmp_path,
        {
            "task_id": "worker-task",
            "objective": "record a provider stop without retry inflation",
            "base_revision": "abc123",
            "allowed_files": ["tests/v2/worker.py"],
            "read_files": ["tests/v2/worker.py"],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["ollama"],
            "outbound_files": ["tests/v2/worker.py"],
            "requirements": [],
            "acceptance": [],
            "test_commands": ["python -m pytest tests/v2/worker.py -q"],
            "max_attempts": max(2, attempt_count),
            "output_contract": {},
        },
    )
    create_plan(
        tmp_path,
        _plan(
            tasks=[
                {
                    "task_id": "worker-task",
                    "owner": "worker",
                    "status": "REJECTED",
                    "ownership": [],
                    "dependencies": [],
                    "manifest_path": ".devfarm/tasks/worker-task.json",
                    "max_attempts": max(2, attempt_count),
                    "attempt_count": attempt_count,
                    "last_attempt_id": "attempt-001",
                    "last_result_status": "failed",
                    "block_reason": "proposal_failed",
                    "assignment": {
                        "provider_id": "ollama",
                        "provider_binding_id": "ollama:local:model-not-installed",
                        "model_id": "model-not-installed:0b",
                    },
                }
            ],
            ownership=[{"task_id": "worker-task", "paths": []}],
            assignments=[
                {
                    "task_id": "worker-task",
                    "owner": "worker",
                    "provider_id": "ollama",
                    "provider_binding_id": "ollama:local:model-not-installed",
                    "model_id": "model-not-installed:0b",
                }
            ],
            dependencies=[{"task_id": "worker-task", "depends_on": []}],
        ),
    )
    runner = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")
    runner.create()

    runner.advance(providers={})

    wakes = runner.plan()["supervisor"]["wake_events"]
    assert [event["kind"] for event in wakes] == [expected_kind]


def test_supervisor_metadata_rejects_raw_conversation_payloads():
    with pytest.raises(ValueError, match="raw"):
        normalize_supervisor_metadata({"wake_events": [{"kind": "x", "raw_output": "secret"}]})


@pytest.mark.parametrize(
    ("block_reason", "expected_action"),
    [
        ("worker_feasibility_reframe_required", "reframe_worker_task"),
        ("worker_feasibility_invalid_acceptance", "repair_worker_manifest"),
        ("proposal_failed", "resolve_blocker"),
    ],
)
def test_supervisor_exposes_feasibility_next_action_without_model_retry(block_reason, expected_action):
    plan = {
        "status": "BLOCKED",
        "tasks": [
            {
                "task_id": "worker-task",
                "owner": "worker",
                "status": "BLOCKED",
                "block_reason": block_reason,
                "last_attempt_id": "attempt-001",
                "canonical_execution": {"logical_execution_id": "logical-001"},
            }
        ],
        "review_decisions": [],
    }

    status, next_action = _canonical_supervisor_action(plan)

    assert status == "BLOCKED"
    assert next_action == expected_action


def test_supervisor_step_exposes_bounded_host_feasibility_context(tmp_path):
    task_id = "worker-task"
    attempt_id = "attempt-001"
    result_ref = result_reference(task_id, attempt_id)
    result_path = tmp_path / result_ref
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "attempt_id": attempt_id,
                "known_issues": ["secret-shaped marker must not be projected"],
                "worker_metrics": {
                    "pre_dispatch_block": "feasibility",
                    "proposal_preflight": {
                        "stage": "worker_feasibility",
                        "failure_class": "FEASIBILITY",
                        "fallback_eligible": False,
                        "fallback_disposition": "reframe_before_provider",
                    },
                    "feasibility": {
                        "task_id": task_id,
                        "status": "reframe_required",
                        "output_mode": "minimal_file_replacement",
                        "input_file_count": 1,
                        "input_bytes": 221838,
                        "input_chars": 200000,
                        "input_lines": 9000,
                        "largest_input_bytes": 221838,
                        "context_file_count": 2,
                        "context_bytes": 331067,
                        "missing_context_files": [],
                        "output_capacity_tokens": 4096,
                        "output_capacity_chars": 16384,
                        "estimated_output_chars": 200512,
                        "reasons": ["input_file_too_large", "output_envelope_too_large"],
                        "recommended_action": "split_input_scope",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    plan = {
        "run_id": "supervisor-test-001",
        "plan_revision": 2,
        "status": "BLOCKED",
        "tasks": [
            {
                "task_id": task_id,
                "owner": "worker",
                "status": "BLOCKED",
                "block_reason": "worker_feasibility_reframe_required",
                "last_attempt_id": attempt_id,
                "result_ref": result_ref,
            }
        ],
        "review_decisions": [],
        "supervisor": {"status": "BLOCKED", "next_action": "reframe_worker_task"},
    }

    step = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")._step(plan)
    projected = step.to_dict()["worker_feasibility"]

    assert projected == [
        {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "block_reason": "worker_feasibility_reframe_required",
            "next_action": "reframe_worker_task",
            "result_ref": result_ref,
            "evidence_status": "available",
            "details": {
                "status": "reframe_required",
                "output_mode": "minimal_file_replacement",
                "recommended_action": "split_input_scope",
                "measurements": {
                    "input_file_count": 1,
                    "input_bytes": 221838,
                    "largest_input_bytes": 221838,
                    "context_file_count": 2,
                    "context_bytes": 331067,
                    "output_capacity_tokens": 4096,
                    "output_capacity_chars": 16384,
                    "estimated_output_chars": 200512,
                },
                "reason_codes": ["input_file_too_large", "output_envelope_too_large"],
            },
            "reframe_handoff_status": "unavailable",
        }
    ]
    assert "secret-shaped marker" not in json.dumps(projected)


def test_supervisor_feasibility_context_fails_closed_when_artifact_identity_mismatches(tmp_path):
    task_id = "worker-task"
    attempt_id = "attempt-current"
    wrong_ref = result_reference(task_id, "attempt-stale")
    wrong_path = tmp_path / wrong_ref
    wrong_path.parent.mkdir(parents=True)
    wrong_path.write_text("{}", encoding="utf-8")
    plan = {
        "run_id": "supervisor-test-001",
        "plan_revision": 2,
        "status": "BLOCKED",
        "tasks": [
            {
                "task_id": task_id,
                "owner": "worker",
                "status": "BLOCKED",
                "block_reason": "worker_feasibility_reframe_required",
                "last_attempt_id": attempt_id,
                "result_ref": wrong_ref,
            }
        ],
        "review_decisions": [],
        "supervisor": {"status": "BLOCKED", "next_action": "reframe_worker_task"},
    }

    projected = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")._step(plan).to_dict()[
        "worker_feasibility"
    ]

    assert projected == [
        {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "block_reason": "worker_feasibility_reframe_required",
            "next_action": "reframe_worker_task",
            "result_ref": result_reference(task_id, attempt_id),
            "evidence_status": "unavailable",
        }
    ]


def _reframe_test_manifest(task_id="worker-task", *, test_commands=None):
    return {
        "task_id": task_id,
        "objective": "Make one bounded, independently verifiable change.",
        "base_revision": "abc123",
        "allowed_files": ["tests/v2/worker.py"],
        "read_files": ["tests/v2/worker.py"],
        "forbidden_files": [],
        "external_provider_allowed": False,
        "approved_provider_ids": [],
        "outbound_files": [],
        "requirements": ["Preserve existing behavior outside the requested change."],
        "acceptance": ["The focused test passes."],
        "test_commands": (
            ["python -m pytest tests/v2/worker.py -q"]
            if test_commands is None
            else test_commands
        ),
        "max_attempts": 1,
        "output_contract": {"mode": "minimal_file_replacement", "max_output_tokens": 4096},
    }


def _write_feasibility_result(
    tmp_path,
    *,
    task_id,
    attempt_id,
    manifest,
    status,
    manifest_sha256=None,
    result_ref=None,
):
    result_ref = result_ref or result_reference(task_id, attempt_id)
    result_path = tmp_path / result_ref
    result_path.parent.mkdir(parents=True, exist_ok=True)
    recommended_action = "split_input_scope" if status == "reframe_required" else "repair_manifest_acceptance"
    result = {
        "status": "failed",
        "attempt_id": attempt_id,
        "base_revision": manifest["base_revision"],
        "changed_files": [],
        "tests_run": [],
        "tests_passed": False,
        "worker_metrics": {
            "pre_dispatch_block": "feasibility",
            "manifest_sha256": manifest_sha256,
            "proposal_preflight": {
                "stage": "worker_feasibility",
                "failure_class": "FEASIBILITY",
                "fallback_eligible": False,
                "fallback_disposition": "reframe_before_provider",
            },
            "feasibility": {
                "task_id": task_id,
                "status": status,
                "output_mode": "minimal_file_replacement",
                "input_file_count": 1,
                "input_bytes": 100,
                "largest_input_bytes": 100,
                "context_file_count": 1,
                "context_bytes": 100,
                "output_capacity_tokens": 4096,
                "output_capacity_chars": 16384,
                "estimated_output_chars": 200,
                "reasons": ["input_file_too_large"] if status == "reframe_required" else ["test_commands_missing"],
                "recommended_action": recommended_action,
            },
        },
    }
    if manifest_sha256 is None:
        result["worker_metrics"].pop("manifest_sha256")
    result_path.write_text(json.dumps(result), encoding="utf-8")
    return result_ref


def _reframe_projection_plan(*, result_ref, attempt_id="attempt-reframe", manifest_ref=".devfarm/tasks/worker-task.json", block_reason="worker_feasibility_reframe_required", task_id="worker-task"):
    return {
        "run_id": "supervisor-test-001",
        "plan_revision": 2,
        "base_revision": "abc123",
        "status": "BLOCKED",
        "tasks": [{
            "task_id": task_id,
            "owner": "worker",
            "status": "BLOCKED",
            "block_reason": block_reason,
            "last_attempt_id": attempt_id,
            "result_ref": result_ref,
            "manifest_path": manifest_ref,
            "ownership": ["tests/v2/worker.py"],
        }],
        "review_decisions": [],
        "supervisor": {"status": "BLOCKED", "next_action": "reframe_worker_task"},
    }


def test_supervisor_reframe_handoff_binds_exact_manifest_and_bounded_constraints(tmp_path):
    task_id = "worker-task"
    attempt_id = "attempt-reframe"
    manifest = _reframe_test_manifest(task_id)
    manifest_path = write_manifest(tmp_path, manifest)
    normalized_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_ref = manifest_path.relative_to(tmp_path).as_posix()
    result_ref = _write_feasibility_result(
        tmp_path,
        task_id=task_id,
        attempt_id=attempt_id,
        manifest=normalized_manifest,
        status="reframe_required",
        manifest_sha256=canonical_digest(normalized_manifest),
    )

    projected = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")._step(
        _reframe_projection_plan(result_ref=result_ref, manifest_ref=manifest_ref)
    ).to_dict()["worker_feasibility"][0]

    handoff = projected["reframe_handoff"]
    assert projected["evidence_status"] == "available"
    assert projected["reframe_handoff_status"] == "available"
    assert handoff["proposal_only"] is True
    assert handoff["source"] == {
        "task_id": task_id,
        "attempt_id": attempt_id,
        "result_ref": result_ref,
        "manifest_ref": manifest_ref,
        "manifest_sha256": canonical_digest(normalized_manifest),
    }
    assert handoff["base_revision"] == "abc123"
    assert handoff["allowed_file_count"] == 1
    assert {
        "preserve_base_revision",
        "candidate_scope_subset_of_source",
        "preserve_acceptance_and_test_obligations",
        "disjoint_active_task_ownership",
        "fresh_task_and_attempt_identity",
        "do_not_retry_blocked_task",
        "host_planning_validation_required",
    } == set(handoff["constraints"])
    assert "Make one bounded" not in json.dumps(handoff)
    assert "secret-shaped" not in json.dumps(handoff)


def test_result_reference_rejects_path_separator_in_attempt_id():
    with pytest.raises(DevFarmError, match="safe identifier characters"):
        result_reference("worker-task", "attempt/nested")


def test_supervisor_reframe_handoff_fails_closed_for_nested_attempt_id(tmp_path):
    task_id = "worker-task"
    attempt_id = "attempt/nested"
    manifest = _reframe_test_manifest(task_id)
    manifest_path = write_manifest(tmp_path, manifest)
    normalized_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_ref = manifest_path.relative_to(tmp_path).as_posix()
    # Simulate a malformed plan plus a matching nested artifact. Repository
    # containment alone permits this path, so the attempt ID must be checked
    # against the canonical artifact identity rules before resolving it.
    result_ref = f".devfarm/results/{task_id}/attempts/{attempt_id}/result.json"
    _write_feasibility_result(
        tmp_path,
        task_id=task_id,
        attempt_id=attempt_id,
        manifest=normalized_manifest,
        status="reframe_required",
        manifest_sha256=canonical_digest(normalized_manifest),
        result_ref=result_ref,
    )

    projected = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")._step(
        _reframe_projection_plan(
            result_ref=result_ref,
            attempt_id=attempt_id,
            manifest_ref=manifest_ref,
        )
    ).to_dict()["worker_feasibility"]

    assert projected == []


@pytest.mark.parametrize("manifest_digest", [None, "0" * 64])
def test_supervisor_reframe_handoff_fails_closed_without_exact_manifest_digest(tmp_path, manifest_digest):
    task_id = "worker-task"
    attempt_id = "attempt-reframe"
    manifest = _reframe_test_manifest(task_id)
    manifest_path = write_manifest(tmp_path, manifest)
    normalized_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_ref = manifest_path.relative_to(tmp_path).as_posix()
    result_ref = _write_feasibility_result(
        tmp_path,
        task_id=task_id,
        attempt_id=attempt_id,
        manifest=normalized_manifest,
        status="reframe_required",
        manifest_sha256=manifest_digest,
    )

    projected = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")._step(
        _reframe_projection_plan(result_ref=result_ref, manifest_ref=manifest_ref)
    ).to_dict()["worker_feasibility"][0]

    assert projected["evidence_status"] == "available"
    assert projected["reframe_handoff_status"] == "unavailable"
    assert "reframe_handoff" not in projected


def test_supervisor_reframe_handoff_fails_closed_for_manifest_task_mismatch(tmp_path):
    task_id = "worker-task"
    attempt_id = "attempt-reframe"
    manifest = _reframe_test_manifest("different-task")
    manifest_path = write_manifest(tmp_path, manifest)
    normalized_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_ref = manifest_path.relative_to(tmp_path).as_posix()
    result_ref = _write_feasibility_result(
        tmp_path,
        task_id=task_id,
        attempt_id=attempt_id,
        manifest=normalized_manifest,
        status="reframe_required",
        manifest_sha256=canonical_digest(normalized_manifest),
    )

    projected = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")._step(
        _reframe_projection_plan(result_ref=result_ref, manifest_ref=manifest_ref)
    ).to_dict()["worker_feasibility"][0]

    assert projected["evidence_status"] == "available"
    assert projected["reframe_handoff_status"] == "unavailable"
    assert "reframe_handoff" not in projected


@pytest.mark.parametrize("revision_source", ["plan", "result"])
def test_supervisor_reframe_handoff_fails_closed_for_stale_base_revision(tmp_path, revision_source):
    task_id = "worker-task"
    attempt_id = "attempt-reframe"
    manifest = _reframe_test_manifest(task_id)
    manifest_path = write_manifest(tmp_path, manifest)
    normalized_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_ref = manifest_path.relative_to(tmp_path).as_posix()
    result_ref = _write_feasibility_result(
        tmp_path,
        task_id=task_id,
        attempt_id=attempt_id,
        manifest=normalized_manifest,
        status="reframe_required",
        manifest_sha256=canonical_digest(normalized_manifest),
    )
    plan = _reframe_projection_plan(result_ref=result_ref, manifest_ref=manifest_ref)
    if revision_source == "plan":
        plan["base_revision"] = "stale-plan-revision"
    else:
        result_path = tmp_path / result_ref
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["base_revision"] = "stale-result-revision"
        result_path.write_text(json.dumps(result), encoding="utf-8")

    projected = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")._step(plan).to_dict()[
        "worker_feasibility"
    ][0]

    assert projected["evidence_status"] == "available"
    assert projected["reframe_handoff_status"] == "unavailable"
    assert "reframe_handoff" not in projected


def test_supervisor_does_not_offer_reframe_handoff_for_invalid_acceptance(tmp_path):
    task_id = "worker-task"
    attempt_id = "attempt-invalid-acceptance"
    manifest = _reframe_test_manifest(task_id, test_commands=[])
    manifest_path = write_manifest(tmp_path, manifest)
    normalized_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_ref = manifest_path.relative_to(tmp_path).as_posix()
    result_ref = _write_feasibility_result(
        tmp_path,
        task_id=task_id,
        attempt_id=attempt_id,
        manifest=normalized_manifest,
        status="invalid_acceptance",
        manifest_sha256=canonical_digest(normalized_manifest),
    )
    plan = _reframe_projection_plan(
        result_ref=result_ref,
        attempt_id=attempt_id,
        manifest_ref=manifest_ref,
        block_reason="worker_feasibility_invalid_acceptance",
    )

    projected = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")._step(plan).to_dict()[
        "worker_feasibility"
    ][0]

    assert projected["details"]["status"] == "invalid_acceptance"
    assert projected["reframe_handoff_status"] == "not_applicable"
    assert "reframe_handoff" not in projected


@pytest.mark.parametrize(
    ("field", "malformed"),
    [
        ("recommended_action", []),
        ("recommended_action", {"unexpected": "object"}),
        ("output_mode", []),
        ("output_mode", {"unexpected": "object"}),
    ],
)
def test_supervisor_feasibility_shape_mismatch_degrades_to_unavailable(tmp_path, field, malformed):
    task_id = "worker-task"
    attempt_id = "attempt-malformed-enum"
    result_ref = result_reference(task_id, attempt_id)
    result_path = tmp_path / result_ref
    result_path.parent.mkdir(parents=True)
    feasibility = {
        "task_id": task_id,
        "status": "reframe_required",
        "output_mode": "minimal_file_replacement",
        "recommended_action": "split_input_scope",
        "reasons": ["input_file_too_large"],
        "input_file_count": 1,
        "input_bytes": 10,
        "largest_input_bytes": 10,
        "context_file_count": 1,
        "context_bytes": 10,
        "output_capacity_tokens": 100,
        "output_capacity_chars": 400,
        "estimated_output_chars": 200,
    }
    feasibility[field] = malformed
    result_path.write_text(
        json.dumps(
            {
                "status": "failed",
                "attempt_id": attempt_id,
                "worker_metrics": {
                    "pre_dispatch_block": "feasibility",
                    "proposal_preflight": {
                        "stage": "worker_feasibility",
                        "failure_class": "FEASIBILITY",
                        "fallback_eligible": False,
                        "fallback_disposition": "reframe_before_provider",
                    },
                    "feasibility": feasibility,
                },
            }
        ),
        encoding="utf-8",
    )
    plan = {
        "run_id": "supervisor-test-001",
        "plan_revision": 2,
        "status": "BLOCKED",
        "tasks": [
            {
                "task_id": task_id,
                "owner": "worker",
                "status": "BLOCKED",
                "block_reason": "worker_feasibility_reframe_required",
                "last_attempt_id": attempt_id,
                "result_ref": result_ref,
            }
        ],
        "review_decisions": [],
        "supervisor": {"status": "BLOCKED", "next_action": "reframe_worker_task"},
    }

    projected = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")._step(plan).to_dict()[
        "worker_feasibility"
    ]

    assert projected == [
        {
            "task_id": task_id,
            "attempt_id": attempt_id,
            "block_reason": "worker_feasibility_reframe_required",
            "next_action": "reframe_worker_task",
            "result_ref": result_ref,
            "evidence_status": "unavailable",
        }
    ]


def test_supervisor_exposes_feasibility_action_for_legacy_plan_projection():
    plan = {
        "status": "BLOCKED",
        "tasks": [
            {
                "task_id": "worker-task",
                "owner": "worker",
                "status": "BLOCKED",
                "block_reason": "worker_feasibility_reframe_required",
            }
        ],
        "review_decisions": [],
        "supervisor": {"status": "BLOCKED", "next_action": "resolve_blocker"},
    }

    status, next_action = _canonical_supervisor_action(plan)

    assert status == "BLOCKED"
    assert next_action == "reframe_worker_task"


def test_supervisor_does_not_hide_unblocked_work_behind_feasibility_action():
    plan = {
        "status": "BLOCKED",
        "tasks": [
            {
                "task_id": "worker-task",
                "owner": "worker",
                "status": "BLOCKED",
                "block_reason": "worker_feasibility_reframe_required",
            },
            {"task_id": "codex-task", "owner": "codex", "status": "READY"},
        ],
        "review_decisions": [],
        "supervisor": {"status": "ACTIVE", "next_action": "execute_codex_task"},
    }

    status, next_action = _canonical_supervisor_action(plan)

    assert status == "ACTIVE"
    assert next_action == "execute_codex_task"


def test_review_packet_is_compact_and_rejects_raw_worker_output():
    packet = normalize_review_packet(
        {
            "task_id": "task-001",
            "attempt_id": "attempt-001",
            "status": "HOST_VERIFIED",
            "changed_files": ["src/example.py"],
            "patch_sha256": "a" * 64,
            "result_ref": ".devfarm/results/task-001/result.json",
            "verification_ref": ".devfarm/results/task-001/verification/v-001.json",
            "verification_summary": {"passed": True, "test_count": 1},
            "known_issues": [],
            "acceptance": ["focused test passes"],
            "artifact_refs": [{"kind": "patch", "path": ".devfarm/results/task-001/patch.diff"}],
        }
    )
    assert packet["patch_sha256"] == "a" * 64
    assert "patch" not in packet
    with pytest.raises(ValueError, match="raw"):
        normalize_review_packet({**packet, "verification_summary": {"stdout": "secret"}})


def test_review_decision_normalization_is_bounded_and_typed():
    decision = normalize_review_decision(
        {
            "decision_id": "review-001",
            "task_id": "task-001",
            "attempt_id": "attempt-001",
            "decision": "REWORK",
            "findings": ["one focused correction is required"],
            "evidence_refs": [{"kind": "verification", "path": ".devfarm/results/task-001/verification/v.json"}],
            "required_correction": "Fix the focused assertion.",
        }
    )
    assert decision["decision"] == "REWORK"
    assert decision["findings"] == ["one focused correction is required"]
    with pytest.raises(ValueError, match="decision"):
        normalize_review_decision({**decision, "decision": "MAYBE"})
    with pytest.raises(ValueError, match="raw"):
        normalize_review_decision({**decision, "evidence_refs": [{"patch": "inline"}]})


def test_supervisor_persists_review_decision_separately_from_wake_request(tmp_path):
    create_plan(tmp_path, _plan())
    runner = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")
    runner.create()

    step = runner.record_review_decision(
        "task-001",
        attempt_id="attempt-001",
        decision="ESCALATE",
        findings=["Human decision is required"],
        evidence_refs=[{"kind": "result", "path": ".devfarm/results/task-001/result.json"}],
    )

    assert step.status == "HUMAN_DECISION_REQUIRED"
    assert step.metrics["codex_review_count"] == 1
    assert step.metrics["codex_review_request_count"] == 0
    assert runner.plan()["review_decisions"][0]["decision"] == "ESCALATE"


def test_unchanged_heartbeat_escalates_after_bounded_checks():
    metadata = normalize_supervisor_metadata({"cadence_minutes": 1, "unchanged_check_limit": 2})

    first = advance_heartbeat(metadata, unchanged=True)
    second = advance_heartbeat(first, unchanged=True)
    third = advance_heartbeat(second, unchanged=True)

    assert first["cadence_minutes"] == 1
    assert second["cadence_minutes"] == 5
    assert third["cadence_minutes"] == 10


def test_supervised_run_creates_durable_wait_metadata_without_integrating(tmp_path):
    create_plan(tmp_path, _plan())
    runner = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")

    created = runner.create(roadmap_reference={"path": "docs/V2_EXECUTION_PLAN.md"}, expected_remaining_seconds=30)
    step = runner.advance(providers={})

    assert created.status == "ACTIVE"
    assert created.cadence_minutes == 1
    assert step.plan_status == "READY"
    assert step.status == "ACTIVE"
    assert step.next_action == "execute_codex_task"
    assert step.metrics["worker_success_count"] == 0
    assert all(task["status"] != "INTEGRATED" for task in runner.plan()["tasks"])


def test_supervised_run_waits_without_llm_polling_until_intervention(tmp_path):
    create_plan(tmp_path, _plan())
    runner = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")
    runner.create(expected_remaining_seconds=30)
    baseline = runner.status()
    calls = []
    sleeps = []

    def fake_advance(**_kwargs):
        calls.append(True)
        if len(calls) == 1:
            return replace(baseline, status="WAITING_FOR_WORKER", next_action="wait_for_worker")
        return replace(baseline, status="REVIEWING", next_action="review_host_verified")

    runner.advance = fake_advance
    monotonic_values = iter((0.0, 0.0, 0.0))
    step = runner.run_until_intervention(
        providers={},
        max_wait_seconds=60,
        sleep_fn=lambda seconds: sleeps.append(seconds),
        monotonic_fn=lambda: next(monotonic_values),
    )

    assert step.status == "REVIEWING"
    assert len(calls) == 2
    assert sleeps == [60.0]


def test_supervised_run_forwards_explicit_local_trial_to_existing_advance(tmp_path):
    create_plan(tmp_path, _plan())
    runner = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")
    runner.create(expected_remaining_seconds=30)
    baseline = runner.status()
    calls = []

    def finish(**kwargs):
        calls.append(kwargs)
        return replace(baseline, status="COMPLETED", next_action="stop")

    runner.advance = finish
    step = runner.run_until_intervention(
        providers={},
        local_trial=True,
        max_wait_seconds=60,
        sleep_fn=lambda _seconds: (_ for _ in ()).throw(AssertionError("completed run must not sleep")),
    )

    assert step.status == "COMPLETED"
    assert len(calls) == 1
    assert calls[0]["local_trial"] is True


def test_supervisor_advance_forwards_local_trial_to_host_child(tmp_path, monkeypatch):
    write_manifest(
        tmp_path,
        {
            "task_id": "worker-task",
            "objective": "exercise the existing local-trial Host child boundary",
            "base_revision": "abc123",
            "allowed_files": ["tests/v2/worker.py"],
            "read_files": ["tests/v2/worker.py"],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["ollama"],
            "outbound_files": ["tests/v2/worker.py"],
            "requirements": [],
            "acceptance": [],
            "test_commands": ["python -m pytest tests/v2/worker.py -q"],
            "max_attempts": 1,
            "output_contract": {},
        },
    )
    create_plan(
        tmp_path,
        _plan(
            tasks=[
                {
                    "task_id": "worker-task",
                    "owner": "worker",
                    "status": "READY",
                    "ownership": [],
                    "dependencies": [],
                    "manifest_path": ".devfarm/tasks/worker-task.json",
                    "assignment": {
                        "provider_id": "ollama",
                        "provider_binding_id": "ollama:local:qwen3.5-9b",
                        "model_id": "qwen3.5:9b",
                    },
                }
            ],
            ownership=[{"task_id": "worker-task", "paths": []}],
            assignments=[
                {
                    "task_id": "worker-task",
                    "owner": "worker",
                    "provider_id": "ollama",
                    "provider_binding_id": "ollama:local:qwen3.5-9b",
                    "model_id": "qwen3.5:9b",
                }
            ],
            dependencies=[{"task_id": "worker-task", "depends_on": []}],
        ),
    )
    runner = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")
    runner.create()
    executor = object()
    executor_calls = []
    monkeypatch.setattr(
        "scripts.devfarm_host_dispatch.create_host_process_executor",
        lambda *args, **kwargs: executor_calls.append((args, kwargs)) or executor,
    )
    host_dispatch_calls = []
    monkeypatch.setattr(
        "src.dev_agent.providers.host_dispatch.route_through_host",
        lambda provider, selected_executor: host_dispatch_calls.append((provider, selected_executor)) or "host-dispatch",
    )
    plan_dispatch_calls = []
    monkeypatch.setattr(
        "scripts.devfarm_supervisor.dispatch_plan",
        lambda *args, **kwargs: plan_dispatch_calls.append(kwargs) or {},
    )

    runner.advance(
        providers={"worker-task": object()},
        execution_boundary="host_process",
        local_trial=True,
    )

    assert executor_calls[0][1]["local_trial"] is True
    assert host_dispatch_calls[0][1] is executor
    assert plan_dispatch_calls[0]["local_trial"] is True


@pytest.mark.parametrize(
    ("status", "next_action"),
    [
        ("REVIEWING", "review_host_verified"),
        ("INTEGRATING", "integrate_verified_worker"),
        ("ACTIVE", "rework_worker"),
    ],
)
def test_supervised_run_returns_immediately_for_codex_action(tmp_path, status, next_action):
    create_plan(tmp_path, _plan())
    runner = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")
    runner.create()
    baseline = runner.status()
    runner.advance = lambda **_kwargs: replace(baseline, status=status, next_action=next_action)

    step = runner.run_until_intervention(
        providers={},
        max_wait_seconds=60,
        sleep_fn=lambda _seconds: (_ for _ in ()).throw(AssertionError("Codex action must not sleep")),
    )

    assert (step.status, step.next_action) == (status, next_action)


def test_supervisor_exposes_codex_owned_ready_task_action_without_sleep(tmp_path):
    create_plan(tmp_path, _plan())
    runner = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")
    runner.create()

    step = runner.run_until_intervention(
        providers={},
        max_wait_seconds=60,
        sleep_fn=lambda _seconds: (_ for _ in ()).throw(AssertionError("Codex task must not sleep")),
    )

    assert step.status == "ACTIVE"
    assert step.next_action == "execute_codex_task"


def test_supervisor_restart_reconstructs_next_action_from_canonical_stage(tmp_path):
    binding = CanonicalExecutionBinding.for_child(
        logical_execution_id="task-001",
        proposal_id="proposal-001",
        child_key="worker-a",
        executor_kind="operation",
    ).with_observation(
        stage=ExecutionLifecycleStage.HOST_VERIFIED,
        attempt_id="attempt-001",
        verification_id="verification-001",
    )
    create_plan(
        tmp_path,
        _plan(
            tasks=[
                {
                    "task_id": "task-001",
                    "owner": "codex",
                    "status": "READY",
                    "ownership": [],
                    "dependencies": [],
                    "assignment": {"owner": "codex"},
                    "canonical_execution": binding.to_dict(),
                }
            ],
            ownership=[{"task_id": "task-001", "paths": []}],
            assignments=[{"task_id": "task-001", "owner": "codex"}],
            dependencies=[{"task_id": "task-001", "depends_on": []}],
            supervisor={"status": "ACTIVE", "next_action": "advance"},
        ),
    )
    first = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")
    first.create()

    # Simulate a process restart with stale heartbeat metadata.  The task
    # status is deliberately stale too; CommanderPlanStore projects it from
    # the canonical binding before the new Supervisor reads it.
    second = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")
    resumed = second.status()

    assert resumed.status == "REVIEWING"
    assert resumed.next_action == "review_host_verified"
    assert second.plan()["tasks"][0]["status"] == "HOST_VERIFIED"


def test_supervisor_resume_preserves_assigned_worker_binding(tmp_path, monkeypatch):
    write_manifest(
        tmp_path,
        {
            "task_id": "worker-task",
            "objective": "preserve the assigned binding",
            "base_revision": "abc123",
            "allowed_files": ["tests/v2/worker.py"],
            "read_files": ["tests/v2/worker.py"],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["gemini"],
            "outbound_files": ["tests/v2/worker.py"],
            "requirements": [],
            "acceptance": [],
            "test_commands": ["python -m pytest tests/v2/worker.py -q"],
            "max_attempts": 1,
            "output_contract": {},
        },
    )
    create_plan(
        tmp_path,
        _plan(
            tasks=[
                {
                    "task_id": "worker-task",
                    "owner": "worker",
                    "status": "READY",
                    "ownership": [],
                    "dependencies": [],
                    "manifest_path": ".devfarm/tasks/worker-task.json",
                    "assignment": {
                        "provider_id": "gemini",
                        "provider_binding_id": "gemini:worker:free-3",
                        "model_id": "gemini-3.6-flash",
                    },
                }
            ],
            ownership=[{"task_id": "worker-task", "paths": []}],
            assignments=[
                {
                    "task_id": "worker-task",
                    "owner": "worker",
                    "provider_id": "gemini",
                    "provider_binding_id": "gemini:worker:free-3",
                    "model_id": "gemini-3.6-flash",
                }
            ],
            dependencies=[{"task_id": "worker-task", "depends_on": []}],
        ),
    )
    calls = []

    def fake_provider(name, model, timeout_seconds, provider_binding_id=None, *, allow_local=False):
        calls.append((name, model, timeout_seconds, provider_binding_id, allow_local))
        return object()

    monkeypatch.setattr("scripts.devfarm_resume.build_worker_provider", fake_provider)

    providers = _providers_for_resume(tmp_path, "supervisor-test-001", 30)

    assert set(providers) == {"worker-task"}
    assert calls == [("gemini", "gemini-3.6-flash", 30, "gemini:worker:free-3", False)]


def test_supervisor_step_exposes_plan_delegation_summary(tmp_path):
    write_manifest(
        tmp_path,
        {
            "task_id": "worker-task",
            "objective": "keep a worker-owned task in the plan summary",
            "base_revision": "abc123",
            "allowed_files": ["tests/v2/worker.py"],
            "read_files": ["tests/v2/worker.py"],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["cloudflare"],
            "outbound_files": ["tests/v2/worker.py"],
            "requirements": [],
            "acceptance": [],
            "test_commands": ["python -m pytest tests/v2/worker.py -q"],
            "max_attempts": 1,
            "output_contract": {},
        },
    )
    plan_input = _plan(
        tasks=[
            {
                "task_id": "worker-task",
                "owner": "worker",
                "status": "INTEGRATED",
                "manifest_path": ".devfarm/tasks/worker-task.json",
                "assignment": {"provider_id": "cloudflare", "model_id": "worker-model"},
            },
            {
                "task_id": "codex-direct",
                "owner": "codex",
                "status": "READY",
                "worker_candidate": True,
                "delegation_reason": "cross_cutting",
                "assignment": {"owner": "codex"},
            },
        ],
        ownership=[
            {"task_id": "worker-task", "paths": []},
            {"task_id": "codex-direct", "paths": []},
        ],
        assignments=[
            {"task_id": "worker-task", "owner": "worker", "provider_id": "cloudflare", "model_id": "worker-model"},
            {"task_id": "codex-direct", "owner": "codex"},
        ],
        dependencies=[
            {"task_id": "worker-task", "depends_on": []},
            {"task_id": "codex-direct", "depends_on": []},
        ],
    )
    create_plan(tmp_path, plan_input)
    step = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001").status()

    assert step.delegation["worker_owned_task_count"] == 1
    assert step.delegation["worker_integrated_task_count"] == 1
    assert step.delegation["codex_owned_task_count"] == 1
    assert step.delegation["codex_direct_implementation_count"] == 1
    assert step.delegation["codex_direct_reasons"] == ["cross_cutting"]
    assert step.to_dict()["delegation"] == dict(step.delegation)


def test_supervisor_ownership_command_exposes_current_file_holders(tmp_path, capsys):
    create_plan(
        tmp_path,
        _plan(
            tasks=[
                {
                    "task_id": "codex-owned-file",
                    "owner": "codex",
                    "ownership": ["src/dev_agent/cli.py"],
                }
            ],
            ownership=[
                {
                    "task_id": "codex-owned-file",
                    "paths": ["src/dev_agent/cli.py"],
                }
            ],
            assignments=[{"task_id": "codex-owned-file", "owner": "codex"}],
            dependencies=[{"task_id": "codex-owned-file", "depends_on": []}],
        ),
    )

    assert supervisor_main(["ownership", "supervisor-test-001", "--root", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert '"path": "src/dev_agent/cli.py"' in output
    assert '"work_address": "1"' in output


def test_supervised_run_wait_budget_is_not_human_decision_and_wake_is_deduplicated(tmp_path):
    create_plan(tmp_path, _plan())
    runner = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")
    runner.create()
    baseline = runner.status()
    runner.advance = lambda **_kwargs: replace(baseline, status="WAITING_FOR_WORKER", next_action="wait_for_worker")

    first = runner.run_until_intervention(
        providers={},
        max_wait_seconds=60,
        sleep_fn=lambda _seconds: None,
        monotonic_fn=iter((0.0, 0.0, 61.0)).__next__,
    )
    second = runner.run_until_intervention(
        providers={},
        max_wait_seconds=60,
        sleep_fn=lambda _seconds: None,
        monotonic_fn=iter((0.0, 61.0)).__next__,
    )

    assert first.status == "WAITING_FOR_WORKER"
    assert first.next_action == "wait_budget_exhausted"
    assert second.status == "WAITING_FOR_WORKER"
    assert second.next_action == "wait_budget_exhausted"
    events = [event for event in runner.plan()["supervisor"]["wake_events"] if event["kind"] == "SUPERVISOR_WAIT_BUDGET_EXHAUSTED"]
    assert len(events) == 1


def test_supervised_run_honors_durable_overall_deadline(tmp_path):
    create_plan(tmp_path, _plan())
    runner = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")
    runner.create(
        overall_deadline=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    )
    calls = []

    def should_not_advance(**_kwargs):
        calls.append(True)
        raise AssertionError("expired supervisor run must not dispatch")

    runner.advance = should_not_advance
    step = runner.run_until_intervention(
        providers={},
        max_wait_seconds=60,
        sleep_fn=lambda _seconds: (_ for _ in ()).throw(AssertionError("expired run must not sleep")),
    )

    assert step.status == "ACTIVE"
    assert step.next_action == "supervisor_overall_deadline"
    assert calls == []


def test_supervisor_cli_is_invokable_as_a_script():
    result = subprocess.run(
        [sys.executable, "scripts/devfarm_supervisor.py", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "bounded Codex supervisor" in result.stdout
