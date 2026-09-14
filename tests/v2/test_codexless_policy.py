from __future__ import annotations

import json

import pytest

from scripts.devfarm import write_manifest, write_result
from scripts.devfarm_commander import create_plan
from scripts.devfarm_supervisor import main as supervisor_main
from src.dev_agent.intelligence.codexless import (
    CodexLessPolicy,
    evaluate_shadow_evidence,
)
from src.dev_agent.intelligence.reviewer_adapter import ReviewProposal


def _shadow_evidence(task_id: str, attempt_id: str) -> dict:
    return {
        "status": "PROPOSAL_ONLY_VERIFIED",
        "source": {"task_id": task_id, "attempt_id": attempt_id},
        "authority": {"reviewer_mode": "shadow", "proposal_only": True},
        "comparison": {
            "agreement": True,
            "false_approve": False,
            "false_reject": False,
            "missed_issue": False,
            "unnecessary_rework": False,
            "evidence_quality": "grounded",
        },
        "raw_worker_conversation_recorded": False,
    }


def _candidate_inputs() -> tuple[dict, dict, dict, ReviewProposal, list[dict]]:
    task = {
        "task_id": "task-doc-1",
        "owner": "worker",
        "worker_candidate": True,
        "status": "HOST_VERIFIED",
        "risk": "normal",
        "sensitivity": "normal",
        "ownership": ["docs/change.md"],
    }
    manifest = {
        "task_id": "task-doc-1",
        "task_type": "documentation",
        "allowed_files": ["docs/change.md"],
        "forbidden_files": [],
        "sensitivity": "normal",
    }
    artifact_ref = {"kind": "verification", "path": ".devfarm/verification/task-doc-1.json"}
    packet = {
        "task_id": "task-doc-1",
        "attempt_id": "attempt-doc-1",
        "status": "HOST_VERIFIED",
        "changed_files": ["docs/change.md"],
        "artifact_refs": [artifact_ref],
        "verification_summary": {
            "host_verified": True,
            "host_tests_passed": True,
            "independent_verification": True,
            "result_accepted": True,
            "verification_trust_level": "TRUSTED_HOST_EXEC",
            "operator_approved": True,
        },
    }
    proposal = ReviewProposal(
        task_id="task-doc-1",
        attempt_id="attempt-doc-1",
        decision="APPROVE_INTEGRATION",
        evidence_refs=(artifact_ref,),
        rationale="The bounded documentation change matches the packet evidence.",
    )
    shadow = [
        _shadow_evidence("task-doc-previous", "attempt-previous"),
        _shadow_evidence("task-test-previous", "attempt-test-previous"),
    ]
    return task, manifest, packet, proposal, shadow


def test_shadow_gate_requires_distinct_grounded_agreements() -> None:
    eligible = evaluate_shadow_evidence(
        [_shadow_evidence("task-a", "attempt-a"), _shadow_evidence("task-b", "attempt-b")]
    )

    assert eligible.eligible is True
    assert eligible.sample_count == 2
    assert eligible.distinct_task_count == 2
    assert eligible.metrics["agreement"] == 2

    insufficient = evaluate_shadow_evidence([_shadow_evidence("task-a", "attempt-a")])
    assert insufficient.eligible is False
    assert "minimum_shadow_evidence" in insufficient.reasons


def test_shadow_gate_rejects_disagreement_or_ungrounded_evidence() -> None:
    bad = _shadow_evidence("task-b", "attempt-b")
    bad["comparison"]["agreement"] = False
    bad["comparison"]["evidence_quality"] = "unmatched_reference"

    result = evaluate_shadow_evidence([_shadow_evidence("task-a", "attempt-a"), bad])

    assert result.eligible is False
    assert "shadow_comparison_not_clean" in result.reasons


def test_codexless_policy_returns_candidate_without_integration_authority() -> None:
    task, manifest, packet, proposal, shadow = _candidate_inputs()

    result = CodexLessPolicy().evaluate(
        task=task,
        manifest=manifest,
        packet=packet,
        proposal=proposal,
        shadow_evidence=shadow,
    )

    assert result.eligible is True
    assert result.status == "CANDIDATE"
    assert result.codex_review_required is False
    assert result.integration_authority == "host_policy"
    assert result.official_branch_auto_merge is False


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("risk", "high", "risk_not_allowed"),
        ("sensitivity", "sensitive", "sensitivity_not_allowed"),
    ],
)
def test_codexless_policy_rejects_high_risk_or_sensitive_task(field: str, value: str, reason: str) -> None:
    task, manifest, packet, proposal, shadow = _candidate_inputs()
    task[field] = value

    result = CodexLessPolicy().evaluate(
        task=task,
        manifest=manifest,
        packet=packet,
        proposal=proposal,
        shadow_evidence=shadow,
    )

    assert result.eligible is False
    assert reason in result.reasons


def test_codexless_policy_rejects_unverified_or_rework_proposal() -> None:
    task, manifest, packet, proposal, shadow = _candidate_inputs()
    packet["verification_summary"]["independent_verification"] = False

    result = CodexLessPolicy().evaluate(
        task=task,
        manifest=manifest,
        packet=packet,
        proposal=proposal,
        shadow_evidence=shadow,
    )
    assert result.eligible is False
    assert "host_verification_incomplete" in result.reasons

    task, manifest, packet, _, shadow = _candidate_inputs()
    rework = ReviewProposal(
        task_id="task-doc-1",
        attempt_id="attempt-doc-1",
        decision="REWORK",
        required_correction="Add the missing acceptance note.",
        rationale="The packet needs one bounded correction.",
    )
    result = CodexLessPolicy().evaluate(
        task=task,
        manifest=manifest,
        packet=packet,
        proposal=rework,
        shadow_evidence=shadow,
    )
    assert result.eligible is False
    assert "review_proposal_not_approved" in result.reasons


def test_codexless_policy_rejects_scope_and_protected_path_changes() -> None:
    task, manifest, packet, proposal, shadow = _candidate_inputs()
    packet["changed_files"] = ["src/outside.py"]

    result = CodexLessPolicy().evaluate(
        task=task,
        manifest=manifest,
        packet=packet,
        proposal=proposal,
        shadow_evidence=shadow,
    )
    assert result.eligible is False
    assert "changed_file_outside_ownership" in result.reasons

    task, manifest, packet, proposal, shadow = _candidate_inputs()
    task["ownership"] = [".github/workflows/ci.yml"]
    manifest["allowed_files"] = [".github/workflows/ci.yml"]
    packet["changed_files"] = [".github/workflows/ci.yml"]
    result = CodexLessPolicy().evaluate(
        task=task,
        manifest=manifest,
        packet=packet,
        proposal=proposal,
        shadow_evidence=shadow,
    )
    assert result.eligible is False
    assert "protected_path" in result.reasons


def test_codexless_supervisor_cli_is_read_only_candidate_boundary(tmp_path, capsys) -> None:
    revision = "d7-test-base"
    task_id = "task-cli-1"
    attempt_id = "attempt-cli-1"
    manifest_path = write_manifest(
        tmp_path,
        {
            "task_id": task_id,
            "task_type": "documentation",
            "objective": "make one bounded documentation change",
            "base_revision": revision,
            "allowed_files": ["docs/change.md"],
            "read_files": ["docs/change.md"],
            "forbidden_files": [],
            "external_provider_allowed": True,
            "approved_provider_ids": ["gemini"],
            "outbound_files": ["docs/change.md"],
            "requirements": [],
            "acceptance": ["the focused check passes"],
            "test_commands": ["python -m pytest tests/v2/test_example.py -q"],
            "max_attempts": 1,
            "output_contract": {},
        },
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    write_result(
        tmp_path,
        {
            "status": "completed",
            "base_revision": revision,
            "changed_files": ["docs/change.md"],
            "tests_run": ["python -m pytest tests/v2/test_example.py -q"],
            "tests_passed": True,
            "known_issues": [],
            "assumptions": [],
            "attempt_id": attempt_id,
            "verification_id": "verification-cli",
        },
        manifest=manifest,
    )
    attempt_root = tmp_path / ".devfarm" / "results" / task_id / "attempts" / attempt_id
    (attempt_root / "patch.diff").write_text("bounded patch evidence", encoding="utf-8")
    (attempt_root / "verification").mkdir()
    (attempt_root / "verification" / "verification-cli.json").write_text(
        json.dumps(
            {
                "verification_id": "verification-cli",
                "verified_at": "2026-09-14T00:00:00+00:00",
                "verified_tests": [{"passed": True}, {"passed": True}],
                "independent_verification": True,
                "containment_level": "TRUSTED_HOST_EXEC",
                "operator_approved": True,
            }
        ),
        encoding="utf-8",
    )
    create_plan(
        tmp_path,
        {
            "run_id": "d7-cli-run",
            "objective": "bounded Codex-less candidate",
            "base_revision": revision,
            "tasks": [
                {
                    "task_id": task_id,
                    "owner": "worker",
                    "status": "HOST_VERIFIED",
                    "task_type": "documentation",
                    "risk": "normal",
                    "sensitivity": "normal",
                    "manifest_path": f".devfarm/tasks/{task_id}.json",
                    "ownership": ["docs/change.md"],
                    "last_attempt_id": attempt_id,
                    "result_ref": f".devfarm/results/{task_id}/attempts/{attempt_id}/result.json",
                    "worker_candidate": True,
                    "assignment": {
                        "provider_id": "gemini",
                        "provider_binding_id": "gemini:worker",
                        "model_id": "gemini-3.6-flash",
                    },
                    "max_attempts": 1,
                }
            ],
        },
    )
    artifact_ref = {
        "kind": "verification",
        "path": f".devfarm/results/{task_id}/attempts/{attempt_id}/verification/verification-cli.json",
    }
    proposal = ReviewProposal(
        task_id=task_id,
        attempt_id=attempt_id,
        decision="APPROVE_INTEGRATION",
        evidence_refs=(artifact_ref,),
        rationale="The bounded packet is independently verified.",
    )
    proposal_file = tmp_path / "proposal.json"
    proposal_file.write_text(json.dumps(proposal.to_dict()), encoding="utf-8")
    shadow = [
        _shadow_evidence("task-previous-a", "attempt-previous-a"),
        _shadow_evidence("task-previous-b", "attempt-previous-b"),
    ]
    evidence_files = []
    for index, item in enumerate(shadow):
        evidence_file = tmp_path / f"shadow-{index}.json"
        evidence_file.write_text(json.dumps(item), encoding="utf-8")
        evidence_files.append(evidence_file)

    # The CLI loads the current packet from the plan's Host artifacts and only
    # evaluates a candidate; it must not manufacture an approval or mutate Git.
    exit_code = supervisor_main(
        [
            "codexless",
            "d7-cli-run",
            task_id,
            "--root",
            str(tmp_path),
            "--proposal-file",
            str(proposal_file),
            "--shadow-evidence-file",
            str(evidence_files[0]),
            "--shadow-evidence-file",
            str(evidence_files[1]),
        ]
    )

    assert exit_code == 0
    assert '"status": "CANDIDATE"' in capsys.readouterr().out
    saved_plan = json.loads((tmp_path / ".devfarm" / "plans" / "d7-cli-run.json").read_text(encoding="utf-8"))
    assert saved_plan["tasks"][0]["status"] == "HOST_VERIFIED"
