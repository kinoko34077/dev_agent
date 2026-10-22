"""M2: advisory provenance is bounded, stable and not an execution decision."""
from __future__ import annotations

import copy

import pytest

from scripts.devfarm_goal_attestation import (
    applicability, definition_sha256, normalize_attestation,
    not_recorded, reviewed_attestation,
)


def sample_plan():
    return {
        "schema_version": 1,
        "run_id": "m2-test-plan",
        "objective": "Run additional DSP models in existing realtime Settings",
        "base_revision": "a" * 40,
        "status": "PLANNED",
        "plan_revision": 0,
        "results": [],
        "review_decisions": [],
        "created_at": "2026-10-22T00:00:00Z",
        "updated_at": "2026-10-22T00:00:00Z",
        "tasks": [{
            "task_id": "design",
            "owner": "codex",
            "ownership": ["docs/dsp.md"],
            "dependencies": [],
            "status": "READY",
            "attempt_count": 0,
            "assignment": {"task_id": "design", "owner": "codex"},
        }],
        "dependencies": [{"task_id": "design", "depends_on": []}],
        "ownership": [{"task_id": "design", "paths": ["docs/dsp.md"]}],
        "assignments": [{"task_id": "design", "owner": "codex"}],
    }


def sample_review(plan):
    return reviewed_attestation(
        plan,
        {"disposition": "ALIGNED", "advisory_only": True,
         "proposal_sha256": "a" * 64,
         "source_refs": [{"ref": "kinoko34077/devflow#761", "sha256": "b" * 64}]},
        {"proposal": "actual complete original plan", "sources": ["source snapshot"]},
    )


def test_reviewed_plan_stays_bound_to_actual_definition():
    plan = sample_plan()
    record = sample_review(plan)
    assert record["state"] == "reviewed_advisory"
    assert record["advisory_only"] is True
    assert applicability(plan, record) == "matches_initial_definition"
    assert normalize_attestation(record) == record


@pytest.mark.parametrize("edit", [
    lambda plan: plan.__setitem__("objective", "Use a standalone offline preview"),
    lambda plan: plan.__setitem__("run_id", "other-plan"),
    lambda plan: plan["tasks"][0].__setitem__("owner", "worker"),
    lambda plan: plan["tasks"][0].__setitem__("ownership", ["docs/offline.md"]),
    lambda plan: plan["tasks"][0].__setitem__("new_semantic_field", "alternate behavior"),
    lambda plan: plan["assignments"][0].__setitem__("owner", "worker"),
])
def test_semantic_definition_change_stales_review(edit):
    plan = sample_plan()
    record = sample_review(plan)
    edit(plan)
    assert applicability(plan, record) == "stale_definition"


def test_normal_execution_progress_does_not_stale_review():
    plan = sample_plan()
    record = sample_review(plan)
    plan["status"] = "INTEGRATED"
    plan["plan_revision"] = 9
    plan["tasks"][0]["attempt_count"] = 2
    plan["tasks"][0]["status"] = "INTEGRATED"
    plan["results"] = [{"task_id": "design", "status": "success"}]
    assert applicability(plan, record) == "matches_initial_definition"


def test_absence_and_explicit_no_review_are_distinct():
    plan = sample_plan()
    assert applicability(plan, None) == "legacy_unknown"
    assert applicability(plan, not_recorded()) == "not_recorded"


def test_private_source_content_and_authority_are_rejected():
    record = sample_review(sample_plan())
    record["source_refs"][0]["text"] = "PRIVATE SOURCE BODY"
    assert applicability(sample_plan(), record) == "invalid"
    with pytest.raises(ValueError):
        normalize_attestation(record)
    record = sample_review(sample_plan())
    record["permission"] = "APPROVE_INTEGRATION"
    with pytest.raises(ValueError):
        normalize_attestation(record)


def test_non_aligned_packet_must_not_be_recorded():
    plan = sample_plan()
    with pytest.raises(ValueError):
        reviewed_attestation(
            plan,
            {"disposition": "REWORK", "advisory_only": True,
             "proposal_sha256": "a" * 64,
             "source_refs": [{"ref": "owner#1", "sha256": "b" * 64}]},
            {"proposal": "wrong"},
        )


def test_json_key_order_does_not_change_definition_hash():
    plan = sample_plan()
    reordered = dict(reversed(list(plan.items())))
    assert definition_sha256(reordered) == definition_sha256(plan)


def test_metadata_has_no_authenticated_identity_or_execution_authority():
    record = sample_review(sample_plan())
    assert record["assessor_ref"] == "unverified:caller_assessed"
    assert "APPROVE_INTEGRATION" not in str(record)
