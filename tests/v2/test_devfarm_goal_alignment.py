import hashlib
import json

import pytest

from scripts.devfarm import main as devfarm_main
from scripts.devfarm_goal_alignment import GoalAlignmentError, evaluate_goal_preflight

REF = "kinoko34077/frequency-warp-vst#273"
SRC = (
    "既存の「設定 → 処理モデル」\n"
    "同じWebAudio AudioWorklet／Rust共有DSPリアルタイム経路\n"
    "別の試聴ボタン・独立モデル比較パネル・1.5秒切り出し・Rust→Lab二段階Workerは**禁止／受入対象外**。"
)


def example(proposal="Existing Settings controls the live shared Rust DSP.", finding="satisfies"):
    goals = {
        "schema_version": 1, "request_ref": REF,
        "higher_goals": [{"kind": "explicit", "statement": "Realtime DSP model control", "evidence_ref": REF}],
        "fixed_constraints": [{"kind": "explicit", "statement": "No isolated preview", "evidence_ref": REF}],
        "success_conditions": ["User changes the actual running processing model"],
    }
    snippets = ["既存の「設定 → 処理モデル」", "別の試聴ボタン・独立モデル比較パネル", "同じWebAudio AudioWorklet"]
    checks = [
        {"target": target, "finding": finding, "source_ref": REF, "source_quote": excerpt,
         "proposal_quote": proposal, "reason": "Assessed against actual realtime operation and the fixed UI contract."}
        for target, excerpt in zip(["goal:0", "fixed:0", "success:0"], snippets)
    ]
    return {
        "schema_version": 1, "goal_context": goals,
        "sources": [{"ref": REF, "text": SRC, "sha256": hashlib.sha256(SRC.encode()).hexdigest()}],
        "proposal": proposal, "assessments": checks,
        "reuse": {"status": "not_applicable", "reason": "No shared numeric component in the present DSP change",
                  "proposal_quote": proposal, "capability_ref": None, "capability_quote": None},
    }


def test_valid_realtime_architecture_is_advisory_aligned():
    result = evaluate_goal_preflight(example())
    assert result["disposition"] == "ALIGNED" and result["advisory_only"]


def test_disallowed_offline_preview_returns_rework():
    case = example("Separate offline preview panel for 1.5 seconds with Rust-to-Lab processing.", "contradicts")
    result = evaluate_goal_preflight(case)
    assert result["disposition"] == "REWORK"
    assert "fixed:0" in result["contradictions"]


def test_missing_coverage_requires_evidence():
    case = example()
    case["assessments"].pop()
    assert evaluate_goal_preflight(case)["disposition"] == "NEEDS_EVIDENCE"


def test_source_digest_and_quotes_are_verified():
    case = example()
    case["sources"][0]["sha256"] = "0" * 64
    with pytest.raises(GoalAlignmentError):
        evaluate_goal_preflight(case)
    case = example()
    case["assessments"][0]["proposal_quote"] = "Fabricated proposal excerpt"
    with pytest.raises(GoalAlignmentError):
        evaluate_goal_preflight(case)


def test_an_inferred_goal_cannot_rewrite_fixed_conditions():
    case = example()
    case["goal_context"]["fixed_constraints"][0]["kind"] = "inferred_hypothesis"
    with pytest.raises(GoalAlignmentError):
        evaluate_goal_preflight(case)
    case = example()
    case["goal_context"]["higher_goals"][0] = {"kind": "inferred_hypothesis", "statement": "Maybe other UI",
                                                  "reason": "Unconfirmed assessor inference"}
    case["assessments"][0]["finding"] = "contradicts"
    result = evaluate_goal_preflight(case)
    assert result["disposition"] == "NEEDS_EVIDENCE"
    assert "accepted_goal_missing" in result["unresolved"]
    assert "goal:0" in result["hypothesis_only"]


def test_legitimate_reuse_and_duplicate_semantics_are_distinguished():
    ref = "kinoko34077/devflow#758"
    text = "Reuse existing functionality where semantics match; separate modules only for truly different semantics."
    case = example()
    case["sources"].append({"ref": ref, "text": text, "sha256": hashlib.sha256(text.encode()).hexdigest()})
    case["reuse"] = {
        "status": "duplicated", "reason": "Copies semantically identical parsing",
        "proposal_quote": case["proposal"], "capability_ref": ref,
        "capability_quote": "Reuse existing functionality",
    }
    assert evaluate_goal_preflight(case)["disposition"] == "REWORK"
    case["reuse"]["status"] = "distinct"
    case["reuse"]["reason"] = "Separate DSP semantics from UI parsing"
    assert evaluate_goal_preflight(case)["disposition"] == "ALIGNED"


def test_cli_and_pre_plan_rejection(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(example()), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(example("Separate offline preview for 1.5 seconds.", "contradicts")), encoding="utf-8")
    assert devfarm_main(["goal-check", str(good)]) == 0
    assert '"ALIGNED"' in capsys.readouterr().out
    assert devfarm_main(["goal-check", str(bad)]) == 1
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit) as err:
        devfarm_main(["plan", str(plan), "--root", str(tmp_path), "--goal-check", str(bad)])
    assert err.value.code == 2
    assert not (tmp_path / ".devfarm").exists()


def test_no_goal_preflight_keeps_existing_plan_flow(tmp_path, monkeypatch):
    from scripts import devfarm_commander
    monkeypatch.setattr(devfarm_commander, "create_plan", lambda root, value: {"unchanged": True})
    plan = tmp_path / "plan.json"
    plan.write_text("{}", encoding="utf-8")
    assert devfarm_main(["plan", str(plan), "--root", str(tmp_path)]) == 0
