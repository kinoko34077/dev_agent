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


def test_source_digest_preserves_leading_and_trailing_whitespace():
    case = example()
    case["sources"][0]["text"] = "\n" + case["sources"][0]["text"] + "\n"
    case["sources"][0]["sha256"] = hashlib.sha256(case["sources"][0]["text"].encode()).hexdigest()
    result = evaluate_goal_preflight(case)
    assert result["source_refs"][0]["sha256"] == case["sources"][0]["sha256"]


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


def test_goal_check_is_bound_to_exact_commander_plan(tmp_path, monkeypatch, capsys):
    from scripts import devfarm_commander
    from scripts.devfarm_goal_alignment import canonical_plan_proposal
    submitted = []
    monkeypatch.setattr(devfarm_commander, "create_plan",
                        lambda root, spec: submitted.append(spec) or {"created": True})
    spec = {
        "run_id": "test-m2-goal-linkage",
        "objective": "Existing settings switch the realtime DSP model; integrate supported Rust realtime DSP",
        "base_revision": "a" * 40,
        "tasks": [
            {"task_id": "t1", "owner": "codex", "task_type": "architecture",
             "ownership": ["docs/realtime-dsp-architecture.md"]},
        ],
    }
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(spec), encoding="utf-8")
    proposal = canonical_plan_proposal(spec)
    evidence = example(proposal=proposal)
    packet_file = tmp_path / "goal.json"
    packet_file.write_text(json.dumps(evidence), encoding="utf-8")

    assert devfarm_main(["goal-plan-text", str(plan_file)]) == 0
    assert capsys.readouterr().out.strip() == proposal
    assert devfarm_main(["plan", str(plan_file), "--goal-check", str(packet_file),
                         "--root", str(tmp_path)]) == 0
    assert submitted == [spec]

    # Reusing a positive report for a different implementation task is forbidden.
    spec["objective"] = "Create an isolated 1.5-second offline demo instead"
    plan_file.write_text(json.dumps(spec), encoding="utf-8")
    with pytest.raises(SystemExit) as err:
        devfarm_main(["plan", str(plan_file), "--goal-check", str(packet_file),
                     "--root", str(tmp_path)])
    assert err.value.code == 2
    assert submitted == [dict(
        run_id="test-m2-goal-linkage",
        objective="Existing settings switch the realtime DSP model; integrate supported Rust realtime DSP",
        base_revision="a" * 40,
        tasks=[{"task_id": "t1", "owner": "codex", "task_type": "architecture",
                "ownership": ["docs/realtime-dsp-architecture.md"]}]
    )]


def test_exact_plan_binding_cannot_ignore_other_tasks_or_objective():
    from scripts.devfarm_goal_alignment import (
        GoalAlignmentError, canonical_plan_proposal, evaluate_plan_goal_preflight,
    )
    spec = {"run_id": "goal-binding-test",
            "objective": "Use realtime DSP path; implement it with existing Settings",
            "base_revision": "a" * 40,
            "tasks": [{"task_id": "a", "owner": "codex",
                       "ownership": ["docs/dsp-design.md"]}]}
    packet = example(proposal=canonical_plan_proposal(spec))
    assert evaluate_plan_goal_preflight(packet, spec)["disposition"] == "ALIGNED"
    for other in (
        {**spec, "objective": "Build offline preview"},
        {**spec, "tasks": [
            {"task_id": "a", "owner": "codex", "ownership": ["docs/dsp-design.md"]},
            {"task_id": "b", "owner": "codex", "ownership": ["docs/offline-preview.md"]}]},
    ):
        with pytest.raises(GoalAlignmentError):
            evaluate_plan_goal_preflight(packet, other)


def test_plan_canonicalization_ignores_json_key_order_not_value_changes():
    from scripts.devfarm_goal_alignment import canonical_plan_proposal
    assert canonical_plan_proposal({"objective": "a", "tasks": [1]}) == canonical_plan_proposal(
        {"tasks": [1], "objective": "a"})
    assert canonical_plan_proposal({"objective": "a", "tasks": [1]}) != canonical_plan_proposal(
        {"objective": "a", "tasks": [2]})


def test_m2_source_assessment_to_actual_durable_commander_plan(tmp_path, capsys):
    """Non-mocked Commander pilot: reject a real bad plan; persist a good one.

    The evidence packet is authored after reading #273. This test does NOT
    infer natural language semantics or establish independent model accuracy.
    """
    import subprocess
    from pathlib import Path
    from scripts.devfarm_goal_alignment import canonical_plan_proposal
    from scripts.devfarm_commander import CommanderPlanStore

    root = tmp_path / "pilot-repo"
    root.mkdir()
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.name", "Goal Pilot"],
        ["git", "config", "user.email", "goal-pilot@example.invalid"],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("Source-backed Commander M2 pilot\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "pilot baseline"], cwd=root, check=True, capture_output=True)
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True,
                              capture_output=True, text=True).stdout.strip()

    def make_plan(run_id, action):
        return {
            "run_id": run_id,
            "objective": "Add actual realtime Rust DSP model through existing Settings; design action: " + action,
            "base_revision": revision,
            "tasks": [{
                "task_id": "codex-architecture-review",
                "owner": "codex",
                "risk": "normal",
                "ownership": ["docs/approved-dsp-design.md"],
                "task_type": "architecture",
            }],
        }

    wrong = make_plan("m2-bad-plan", "Add an independent 1.5-second Rust-to-Lab preview panel")
    wrong_packet = example(proposal=canonical_plan_proposal(wrong), finding="contradicts")
    bad_plan_path = tmp_path / "bad-plan.json"
    bad_packet_path = tmp_path / "bad-evidence.json"
    bad_plan_path.write_text(json.dumps(wrong), encoding="utf-8")
    bad_packet_path.write_text(json.dumps(wrong_packet), encoding="utf-8")
    with pytest.raises(SystemExit) as denied:
        devfarm_main(["plan", str(bad_plan_path), "--root", str(root),
                      "--goal-check", str(bad_packet_path)])
    assert denied.value.code == 2
    assert not (root / ".devfarm" / "plans" / "m2-bad-plan.json").exists()

    correct = make_plan("m2-good-plan", "Extend existing Settings to switch realtime AudioWorklet/Rust DSP model")
    good_packet = example(proposal=canonical_plan_proposal(correct), finding="satisfies")
    good_plan_path = tmp_path / "good-plan.json"
    good_packet_path = tmp_path / "good-evidence.json"
    good_plan_path.write_text(json.dumps(correct), encoding="utf-8")
    good_packet_path.write_text(json.dumps(good_packet), encoding="utf-8")
    assert devfarm_main(["plan", str(good_plan_path), "--root", str(root),
                         "--goal-check", str(good_packet_path)]) == 0
    capsys.readouterr()
    created = CommanderPlanStore(root).load("m2-good-plan")
    assert created["objective"] == correct["objective"]
    assert len(created["tasks"]) == 1
    assert created["tasks"][0]["task_id"] == "codex-architecture-review"
    assert (root / ".devfarm" / "plans" / "m2-good-plan.json").exists()
    assert not (root / ".devfarm" / "plans" / "m2-bad-plan.json").exists()


def test_goal_preflight_rejects_task_details_dropped_by_commander_normalization():
    """A positive-looking assessment must not pass on discarded design text."""
    from scripts.devfarm_goal_alignment import canonical_plan_proposal, evaluate_plan_goal_preflight
    candidate = {
        "run_id": "m2-unpersisted-content",
        "objective": "Enable realtime models in Settings",
        "base_revision": "a" * 40,
        "tasks": [{
            "task_id": "architect",
            "owner": "codex",
            "ownership": ["docs/dsp.md"],
            "description": "Create forbidden separate offline preview",  # not persisted by Commander
        }],
    }
    packet = example(proposal=canonical_plan_proposal(candidate))
    with pytest.raises(GoalAlignmentError, match="unpersisted reviewed task field"):
        evaluate_plan_goal_preflight(packet, candidate)


def test_substantive_plan_missing_goal_evidence_warns_without_blocking(tmp_path, capsys, monkeypatch):
    """A risky design cannot silently look goal-reviewed; this is advisory."""
    from scripts import devfarm_commander
    received = []
    monkeypatch.setattr(devfarm_commander, "create_plan",
                        lambda root, plan: received.append(plan) or {"accepted": True})
    plan = {
        "run_id": "design-visibility",
        "objective": "Change realtime settings user interaction",
        "base_revision": "a" * 40,
        "tasks": [{
            "task_id": "design",
            "owner": "codex",
            "risk": "high",
            "task_type": "ux",
            "ownership": ["docs/design.md"],
        }],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    assert devfarm_main(["plan", str(path), "--root", str(tmp_path)]) == 0
    stream = capsys.readouterr()
    warning = json.loads(stream.err.strip())
    assert warning["warning"] == "M2_GOAL_REVIEW_NOT_RECORDED"
    assert warning["run_id"] == "design-visibility"
    assert warning["reasons"] == ["design_or_user_interaction", "high_or_critical_risk"]
    assert warning["advisory_only"]
    assert received == [plan]
    assert '"accepted"' in stream.out


def test_minor_legacy_plan_does_not_trigger_goal_review_warning(tmp_path, capsys, monkeypatch):
    from scripts import devfarm_commander
    monkeypatch.setattr(devfarm_commander, "create_plan", lambda root, plan: {"ok": True})
    plan = {
        "run_id": "minor-review",
        "objective": "Fix one small typo",
        "base_revision": "a" * 40,
        "tasks": [{"task_id": "typo", "owner": "codex", "risk": "low",
                   "task_type": "docs", "ownership": ["docs/readme.md"]}],
    }
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    assert devfarm_main(["plan", str(path), "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().err == ""


def test_m2_visibility_is_not_a_semantic_safety_gate():
    from scripts.devfarm_goal_alignment import goal_review_visibility
    # Source/meaning is not deduced from title alone; this is coverage
    # visibility for explicitly typed substantive tasks, not a guarantee.
    assert goal_review_visibility({"tasks": [{"risk": "high"}]}) == ["high_or_critical_risk"]
    assert goal_review_visibility({"tasks": [{"task_type": "architecture"}]}) == [
        "design_or_user_interaction"
    ]
    assert goal_review_visibility({"tasks": [{"task_type": "worker", "risk": "low"}]}) == []
    assert goal_review_visibility({"tasks": "invalid"}) == []


def test_reviewed_ownership_cannot_be_rewritten_by_top_level_plan_ownership():
    """Prior M2 validation only checked key presence, not the saved value."""
    from scripts.devfarm_goal_alignment import (
        GoalAlignmentError, canonical_plan_proposal, evaluate_plan_goal_preflight,
    )
    plan = {
        "run_id": "m2-ownership-drift", "base_revision": "a" * 40,
        "objective": "Change only the reviewed realtime DSP design file",
        "tasks": [{"task_id": "a", "owner": "codex",
                   "ownership": ["docs/reviewed-dsp.md"]}],
        "ownership": [{"task_id": "a", "paths": ["docs/unreviewed-offline-demo.md"]}],
    }
    evidence = example(proposal=canonical_plan_proposal(plan))
    with pytest.raises(GoalAlignmentError, match="rewritten reviewed task field.*ownership"):
        evaluate_plan_goal_preflight(evidence, plan)


def test_reviewed_dependencies_cannot_be_rewritten_by_top_level_plan_dependencies():
    from scripts.devfarm_goal_alignment import (
        GoalAlignmentError, canonical_plan_proposal, evaluate_plan_goal_preflight,
    )
    plan = {
        "run_id": "m2-dependency-drift", "base_revision": "a" * 40,
        "objective": "Verify existing DSP before next architectural step",
        "tasks": [
            {"task_id": "pre", "owner": "codex", "ownership": ["docs/pre.md"]},
            {"task_id": "build", "owner": "codex", "ownership": ["docs/build.md"],
             "dependencies": ["pre"]},
        ],
        "dependencies": [
            {"task_id": "pre", "depends_on": []},
            {"task_id": "build", "depends_on": []},
        ],
    }
    evidence = example(proposal=canonical_plan_proposal(plan))
    with pytest.raises(GoalAlignmentError, match="rewritten reviewed task field.*dependencies"):
        evaluate_plan_goal_preflight(evidence, plan)


def test_consistent_dual_task_and_plan_ownership_remains_accepted():
    from scripts.devfarm_goal_alignment import canonical_plan_proposal, evaluate_plan_goal_preflight
    plan = {
        "run_id": "m2-consistent-ownership", "base_revision": "a" * 40,
        "objective": "Keep the approved DSP file ownership scope",
        "tasks": [{"task_id": "a", "owner": "codex", "ownership": ["docs/dsp.md"]}],
        "ownership": [{"task_id": "a", "paths": ["docs/dsp.md"]}],
    }
    evidence = example(proposal=canonical_plan_proposal(plan))
    assert evaluate_plan_goal_preflight(evidence, plan)["disposition"] == "ALIGNED"
