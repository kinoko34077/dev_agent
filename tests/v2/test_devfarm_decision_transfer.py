"""Synthetic transfer tests, not claims about a completed live agent outcome.

Origin evidence: public postmortem #31 (runtime absent, build configuration
present, repeated unchanged runtime checks). Independent target #203 is still
cause-UNVERIFIED; the test deliberately must not infer its root cause.
"""
import json

import pytest

from scripts.devfarm_decision_transfer import DecisionTransferError, evaluate_decision_transfer


ORIGIN = "https://github.com/kinoko34077/kinotch-judgment-learning/issues/31"
TARGET = "https://github.com/kinoko34077/gesture-ime/issues/203"


def observation(oid, stage, state, kind="user_report", ref=TARGET):
    return {"id": oid, "stage": stage, "state": state,
            "evidence_type": kind, "source_ref": ref}


def packet():
    return {
        "schema_version": 1,
        "origin": {
            "ref": ORIGIN,
            "goal": "Complete real GitHub OAuth owner login, not only a CI/deploy pass",
            "success_condition": "Owner login callback and usable owner-only app",
            "observations": [
                observation("build_ui", "producer", "present", "direct_observation", ORIGIN),
                observation("runtime_api", "consumer", "missing", "direct_observation", ORIGIN),
            ],
            "attempts": [
                {"probe": "check-runtime-secrets", "revision": "runtime-v1",
                 "result_observation_id": "runtime_api"},
                {"probe": "check-runtime-secrets", "revision": "runtime-v1",
                 "result_observation_id": "runtime_api"},
            ],
            "cause": {"mechanism": "producer_consumer_scope_mismatch",
                      "status": "source_supported", "source_ref": ORIGIN},
            "correction_ref": ORIGIN,
        },
        "target": {
            "ref": TARGET,
            # The user reported saved settings and an unchanged keyboard, but
            # installed artifact and extension read/write state were not verified.
            "observations": [
                observation("saved_ui", "producer", "present"),
                observation("keyboard", "consumer", "missing"),
            ],
            "attempts": [],
            "fixed_constraints": ["Do not change signing or credentials as a diagnosis shortcut"],
        },
    }


def test_origin_repetition_is_visible_but_rule_is_only_candidate():
    result = evaluate_decision_transfer(packet())
    assert result["origin_redundant_attempts"] == 1
    assert result["rule_status"] == "candidate"
    assert result["disposition"] == "NEEDS_EVIDENCE"
    assert result["next_probe"] == "measure_producer_and_consumer_in_same_revision"
    assert result["advisory_only"] is True


def test_cross_repo_true_boundary_observation_selects_new_probe_in_synthetic_case():
    example = packet()
    # Hypothetical later evidence, NOT an actual verified observation of #203.
    for item in example["target"]["observations"]:
        item["evidence_type"] = "direct_observation"
    result = evaluate_decision_transfer(example)
    assert result["disposition"] == "PROBE_BOUNDARY"
    assert result["next_probe"] == "trace_producer_to_consumer_scope"


def test_counterexample_consumer_present_does_not_transfer():
    example = packet()
    example["target"]["observations"][1]["state"] = "present"
    assert evaluate_decision_transfer(example)["disposition"] == "NOT_APPLICABLE"


def test_counterexample_producer_missing_does_not_transfer():
    example = packet()
    example["target"]["observations"][0]["state"] = "missing"
    assert evaluate_decision_transfer(example)["disposition"] == "NOT_APPLICABLE"


def test_code_inspection_does_not_count_as_runtime_producer_observation():
    example = packet()
    example["target"]["observations"][0]["evidence_type"] = "code_inspection"
    result = evaluate_decision_transfer(example)
    assert result["disposition"] == "NEEDS_EVIDENCE"
    assert result["next_probe"] == "establish_both_boundary_endpoints"


def test_agent_report_alone_is_not_equivalent_to_direct_evidence():
    example = packet()
    for item in example["target"]["observations"]:
        item["evidence_type"] = "agent_report"
    assert evaluate_decision_transfer(example)["disposition"] == "NEEDS_EVIDENCE"


def test_conflicting_current_claims_need_reconciliation_not_fabricated_root_cause():
    example = packet()
    example["target"]["observations"].append(observation("also_saved", "producer", "missing"))
    result = evaluate_decision_transfer(example)
    assert result["disposition"] == "NEEDS_EVIDENCE"
    assert result["next_probe"] == "reconcile_conflicting_observations"


def test_same_probe_and_revision_requires_change_of_diagnostic_path():
    example = packet()
    example["target"]["attempts"] = [
        {"probe": "repeat-save", "revision": "same-deployment", "result_observation_id": "keyboard"},
        {"probe": "repeat-save", "revision": "same-deployment", "result_observation_id": "keyboard"},
    ]
    assert evaluate_decision_transfer(example)["avoid_same_probe_without_new_revision"]
    example["target"]["attempts"][1]["revision"] = "new-deployment"
    assert not evaluate_decision_transfer(example)["avoid_same_probe_without_new_revision"]


def test_training_incident_is_not_held_out_case():
    example = packet()
    example["target"]["ref"] = ORIGIN
    with pytest.raises(DecisionTransferError, match="own transfer"):
        evaluate_decision_transfer(example)


def test_unsubstantiated_origin_does_not_mint_rule():
    example = packet()
    example["origin"]["observations"][0]["state"] = "missing"
    with pytest.raises(DecisionTransferError, match="source incident"):
        evaluate_decision_transfer(example)


def test_bad_source_type_missing_field_and_duplicate_evidence_fail_closed():
    example = packet()
    example["target"]["observations"][0]["evidence_type"] = "certified_by_ai"
    with pytest.raises(DecisionTransferError):
        evaluate_decision_transfer(example)
    example = packet()
    del example["origin"]["correction_ref"]
    with pytest.raises(DecisionTransferError):
        evaluate_decision_transfer(example)
    example = packet()
    example["target"]["observations"][1]["id"] = "saved_ui"
    with pytest.raises(DecisionTransferError):
        evaluate_decision_transfer(example)


def test_private_data_is_not_required_or_exposed():
    example = packet()
    result = json.dumps(evaluate_decision_transfer(example))
    assert "GITHUB_CLIENT_SECRET" not in result
    assert "no_dispatch_no_merge_no_permission" in result
