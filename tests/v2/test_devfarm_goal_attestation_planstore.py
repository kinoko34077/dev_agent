"""M2 PlanStore round-trip: advisory provenance is data, never execution authority."""
import copy
import json
import subprocess
from pathlib import Path

import pytest

from scripts.devfarm_goal_attestation import (
    applicability, not_recorded, reviewed_attestation,
)
from scripts.devfarm_plan_state import CommanderPlanStore
from scripts.devfarm_plan_validation import validate_plan
from scripts.devfarm_errors import DevFarmError


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.name", "M2 Plan Test"],
        ["git", "config", "user.email", "m2-plan@example.invalid"],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("M2 durable provenance test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=root, check=True, capture_output=True)
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()
    return root, sha


def _plan(base):
    return {
        "run_id": "m2-real-store-bridge",
        "objective": "Use the existing Settings for continuous realtime DSP",
        "base_revision": base,
        "tasks": [{
            "task_id": "design", "owner": "codex", "task_type": "architecture",
            "ownership": ["docs/approved-design.md"],
        }],
    }


def _attestation(root_plan):
    normalized = validate_plan(root_plan)
    return reviewed_attestation(
        normalized,
        {"disposition": "ALIGNED", "advisory_only": True,
         "proposal_sha256": "a"*64,
         "source_refs": [{"ref": "owner/repo#1", "sha256": "b"*64}]},
        {"schema_version": 1, "proposal": "exact prior plan", "sources": ["source-ref"]},
    )


def test_review_record_roundtrips_real_planstore_and_lifecycle(tmp_path):
    root, base = _repo(tmp_path)
    spec = _plan(base)
    record = _attestation(spec)
    store = CommanderPlanStore(root)
    created = store.create({**spec, "goal_attestation": record})
    assert created["goal_attestation"] == record
    loaded = store.load(spec["run_id"])
    assert loaded["goal_attestation"] == record
    assert applicability(loaded, record) == "matches_initial_definition"
    loaded["plan_revision"] = created["plan_revision"]
    store.save(loaded)
    saved = store.load(spec["run_id"])
    assert saved["goal_attestation"] == record
    assert applicability(saved, record) == "matches_initial_definition"
    stored_text = store.path_for(spec["run_id"]).read_text(encoding="utf-8")
    assert "PRIVATE BODY" not in stored_text


def test_review_becomes_stale_when_plan_purpose_changes(tmp_path):
    root, base = _repo(tmp_path)
    spec = _plan(base)
    record = _attestation(spec)
    store = CommanderPlanStore(root)
    store.create({**spec, "goal_attestation": record})
    loaded = store.load(spec["run_id"])
    loaded["objective"] = "Create separate offline audition instead"
    store.save(loaded)
    saved = store.load(spec["run_id"])
    assert saved["goal_attestation"] == record  # historical fact retained
    assert applicability(saved, record) == "stale_definition"


def test_legacy_and_explicit_no_review_are_not_aligned(tmp_path):
    root, base = _repo(tmp_path)
    store = CommanderPlanStore(root)
    legacy = _plan(base)
    store.create(legacy)
    assert applicability(store.load(legacy["run_id"]), None) == "legacy_unknown"
    current = _plan(base)
    current["run_id"] = "m2-no-review"
    current["tasks"][0]["ownership"] = ["docs/other.md"]
    store.create({**current, "goal_attestation": not_recorded()})
    saved = store.load(current["run_id"])
    assert saved["goal_attestation"] == not_recorded()
    assert applicability(saved, saved["goal_attestation"]) == "not_recorded"


def test_invalid_metadata_rejected_without_creating_plan(tmp_path):
    root, base = _repo(tmp_path)
    spec = _plan(base)
    record = _attestation(spec)
    record["source_refs"][0]["text"] = "PRIVATE BODY"
    store = CommanderPlanStore(root)
    with pytest.raises(DevFarmError, match="goal_attestation"):
        store.create({**spec, "goal_attestation": record})
    assert not store.path_for(spec["run_id"]).exists()


def test_json_schema_declares_optional_non_authoritative_metadata():
    schema_path = Path(__file__).resolve().parents[2] / "spec/v2/DEVFARM_COMMANDER_PLAN_SCHEMA.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert "goal_attestation" in schema["properties"]
    assert "goal_attestation" not in schema["required"]
    variant = schema["$defs"]["goal_attestation"]["oneOf"]
    assert {part["properties"]["state"]["const"] for part in variant} == {
        "not_recorded", "reviewed_advisory"
    }
    assert all(part["additionalProperties"] is False for part in variant)
