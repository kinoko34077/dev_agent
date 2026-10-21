"""Source-anchored, advisory goal-alignment preflight for development plans.

A trusted Codex/reviewer supplies semantic assessments after inspecting the
owning issue and proposal. This module validates completeness, evidence anchors,
provenance, and an advisory disposition. It does NOT infer natural-language
entailment, fetch authority, grant execution rights, or certify runtime UX.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

_ALLOWED_KINDS = {"explicit", "source_derived", "inferred_hypothesis"}
_ALLOWED_CONSTRAINTS = {"explicit", "accepted_spec"}
_FINDINGS = {"satisfies", "contradicts", "unresolved"}
_REUSE = {"not_applicable", "reused", "extended", "distinct", "duplicated", "unknown"}
_MAX_SOURCES = 8
_MAX_CHECKS = 48


class GoalAlignmentError(ValueError):
    """Invalid or misleading evidence packet (not a semantic verdict)."""


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GoalAlignmentError(f"{name} must be an object")
    return dict(value)


def _text(value: object, name: str, *, max_length: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise GoalAlignmentError(f"{name} must be nonempty text (max {max_length})")
    return value.strip()


def _source_body(value: object, name: str) -> str:
    """Validate source content without normalizing bytes used for its digest."""
    if not isinstance(value, str) or not value.strip() or len(value) > 100_000:
        raise GoalAlignmentError(f"{name} must be nonempty source text (max 100000)")
    return value


def _bounded(value: object, name: str, limit: int) -> list[Any]:
    if not isinstance(value, list) or len(value) > limit:
        raise GoalAlignmentError(f"{name} must be a list of at most {limit} entries")
    return value


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _docs(raw: object) -> tuple[dict[str, str], list[dict[str, str]]]:
    source_text: dict[str, str] = {}
    public_docs: list[dict[str, str]] = []
    for index, source in enumerate(_bounded(raw, "sources", _MAX_SOURCES)):
        item = _object(source, f"sources[{index}]")
        if set(item) != {"ref", "text", "sha256"}:
            raise GoalAlignmentError("source requires only ref, text and sha256")
        ref = _text(item["ref"], "source.ref", max_length=500)
        body = _source_body(item["text"], "source.text")
        digest = _text(item["sha256"], "source.sha256", max_length=64)
        if ref in source_text or digest != _sha256(body):
            raise GoalAlignmentError("source identity duplicated or digest does not match supplied text")
        source_text[ref] = body
        public_docs.append({"ref": ref, "sha256": digest})
    return source_text, public_docs


def evaluate_goal_preflight(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate assessor-authored semantic findings against exact supplied sources.

    A PASS only means that the assessor explicitly grounded every accepted
    requirement. The source still must be fetched by the trusted caller at
    the appropriate current revision; quotes alone do not prove correctness.
    """

    payload = _object(packet, "packet")
    expected_keys = {"schema_version", "goal_context", "sources", "proposal", "assessments", "reuse"}
    if set(payload) != expected_keys or type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
        raise GoalAlignmentError("unsupported preflight packet schema or fields")
    context = _object(payload["goal_context"], "goal_context")
    if context.get("schema_version") != 1 or not isinstance(context.get("request_ref"), str):
        raise GoalAlignmentError("goal_context must be M1 v1 source-linked context")
    proposal = _text(payload["proposal"], "proposal", max_length=16_000)
    sources, source_refs = _docs(payload["sources"])

    goal_items = _bounded(context.get("higher_goals", []), "higher_goals", 16)
    constraints = _bounded(context.get("fixed_constraints", []), "fixed_constraints", 16)
    success = _bounded(context.get("success_conditions", []), "success_conditions", 16)
    if not goal_items and context.get("parent_goal_ref"):
        raise GoalAlignmentError("parent goal must be resolved by trusted caller before M2 assessment")
    if not goal_items or not success:
        raise GoalAlignmentError("nontrivial M2 needs an accepted goal and observable success")
    request_ref = _text(context["request_ref"], "request_ref", max_length=500)
    if request_ref not in sources:
        raise GoalAlignmentError("current owning Issue source is missing")

    requirements: dict[str, tuple[str, bool]] = {}
    for index, raw in enumerate(goal_items):
        goal = _object(raw, "higher_goals item")
        kind = _text(goal.get("kind"), "higher_goals.kind", max_length=32)
        if kind not in _ALLOWED_KINDS:
            raise GoalAlignmentError("unsupported higher goal provenance")
        _text(goal.get("statement"), "higher_goals.statement")
        ref = goal.get("evidence_ref")
        if kind == "inferred_hypothesis":
            _text(goal.get("reason"), "inferred higher goal reason")
            requirements[f"goal:{index}"] = (request_ref, False)
        else:
            source_ref = _text(ref, "higher_goals.evidence_ref", max_length=500)
            if source_ref not in sources:
                raise GoalAlignmentError("higher goal authoritative source is missing")
            requirements[f"goal:{index}"] = (source_ref, True)
    for index, raw in enumerate(constraints):
        clause = _object(raw, "fixed_constraints item")
        if clause.get("kind") not in _ALLOWED_CONSTRAINTS:
            raise GoalAlignmentError("inferred goal cannot create a fixed constraint")
        _text(clause.get("statement"), "fixed constraint statement")
        source_ref = _text(clause.get("evidence_ref"), "fixed constraint evidence_ref", max_length=500)
        if source_ref not in sources:
            raise GoalAlignmentError("fixed constraint original source is missing")
        requirements[f"fixed:{index}"] = (source_ref, True)
    for index, clause in enumerate(success):
        _text(clause, "success_conditions entry")
        requirements[f"success:{index}"] = (request_ref, True)

    assessments = _bounded(payload["assessments"], "assessments", _MAX_CHECKS)
    records = {}
    adverse: list[str] = []
    unresolved: list[str] = []
    advisory: list[str] = []
    for index, raw in enumerate(assessments):
        item = _object(raw, f"assessments[{index}]")
        if set(item) != {"target", "finding", "source_ref", "source_quote", "proposal_quote", "reason"}:
            raise GoalAlignmentError("assessment must contain exact target, finding, two anchored quotes, reason")
        target = _text(item["target"], "assessment.target", max_length=60)
        if target in records or target not in requirements:
            raise GoalAlignmentError("duplicate or unknown assessment target")
        expected_source, binding = requirements[target]
        if _text(item["source_ref"], "assessment.source_ref", max_length=500) != expected_source:
            raise GoalAlignmentError("assessment cites wrong authoritative source")
        source_quote = _text(item["source_quote"], "assessment.source_quote", max_length=1800)
        proposal_quote = _text(item["proposal_quote"], "assessment.proposal_quote", max_length=1800)
        if source_quote not in sources[expected_source] or proposal_quote not in proposal:
            raise GoalAlignmentError("assessment uses ungrounded source/proposal quotation")
        finding = _text(item["finding"], "assessment.finding", max_length=24)
        if finding not in _FINDINGS:
            raise GoalAlignmentError("unrecognized finding")
        _text(item["reason"], "assessment.reason", max_length=1800)
        records[target] = {"target": target, "finding": finding, "reason": item["reason"],
                           "source_ref": expected_source, "source_sha256": _sha256(sources[expected_source])}
        if not binding:
            advisory.append(target)
        elif finding == "contradicts":
            adverse.append(target)
        elif finding == "unresolved":
            unresolved.append(target)
    missing = sorted(set(requirements) - set(records))
    if missing:
        unresolved.extend(missing)
    # An AI-only hypothetical purpose is not an accepted basis for "ALIGNED".
    if not any(_object(g, "goal").get("kind") != "inferred_hypothesis" for g in goal_items):
        unresolved.append("accepted_goal_missing")

    reuse = _object(payload["reuse"], "reuse")
    if set(reuse) != {"status", "reason", "proposal_quote", "capability_ref", "capability_quote"}:
        raise GoalAlignmentError("reuse must declare assessment and capability evidence")
    reuse_status = _text(reuse["status"], "reuse.status", max_length=32)
    if reuse_status not in _REUSE:
        raise GoalAlignmentError("invalid reuse classification")
    _text(reuse["reason"], "reuse.reason", max_length=1000)
    reuse_proposal_quote = _text(reuse["proposal_quote"], "reuse.proposal_quote", max_length=1800)
    if reuse_proposal_quote not in proposal:
        raise GoalAlignmentError("reuse proposal quote is not in supplied design")
    capability_ref = reuse["capability_ref"]
    capability_quote = reuse["capability_quote"]
    if reuse_status not in {"not_applicable", "unknown"}:
        cap_ref = _text(capability_ref, "reuse.capability_ref", max_length=500)
        cap_quote = _text(capability_quote, "reuse.capability_quote", max_length=1800)
        if cap_ref not in sources or cap_quote not in sources[cap_ref]:
            raise GoalAlignmentError("reuse capability contract is not anchored in source")
    elif capability_ref is not None or capability_quote is not None:
        raise GoalAlignmentError("unresolved/non-applicable reuse cannot fake capability source")
    if reuse_status == "duplicated":
        adverse.append("reuse")
    elif reuse_status == "unknown":
        unresolved.append("reuse")

    # This is a triage/evidence disposition, never a permission or merge gate.
    disposition = "REWORK" if adverse else "NEEDS_EVIDENCE" if unresolved else "ALIGNED"
    return {
        "schema_version": 1,
        "disposition": disposition,
        "advisory_only": True,
        "assessor_responsibility": "semantic judgments supplied by trusted reviewing agent",
        "source_refs": source_refs,
        "proposal_sha256": _sha256(proposal),
        "checked": [records[key] for key in sorted(records)],
        "contradictions": sorted(adverse),
        "unresolved": sorted(unresolved),
        "hypothesis_only": sorted(advisory),
        "reuse_status": reuse_status,
    }




def goal_review_visibility(plan: Mapping[str, Any]) -> list[str]:
    """Return reasons to ask for M2 evidence, without granting or denying work.

    A small deterministic *coverage signal*, not an NLP classifier, mandatory
    permission gate, or proof that the agent actually considered the user goal.
    Ordinary/legacy low-risk plans remain unaffected.
    """
    if not isinstance(plan, Mapping):
        return []
    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        return []
    reasons: set[str] = set()
    for task in tasks:
        if not isinstance(task, Mapping):
            continue  # existing Commander plan validator remains authoritative
        risk = task.get("risk", "normal")
        kind = task.get("task_type", "worker")
        if isinstance(risk, str) and risk.lower() in {"high", "critical"}:
            reasons.add("high_or_critical_risk")
        if isinstance(kind, str) and kind.lower() in {"architecture", "design", "ui", "ux"}:
            reasons.add("design_or_user_interaction")
    return sorted(reasons)


def canonical_plan_proposal(plan: Mapping[str, Any]) -> str:
    """One unambiguous text representation of the *entire* pending plan spec.

    The reviewer must assess this exact text, not a freehand summary that could
    conceal changed tasks. A plan changed after the assessment cannot reuse it.
    No Commander plan is persisted by this pure function.
    """
    value = _object(plan, "plan")
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise GoalAlignmentError("plan must be canonicalizable JSON") from exc
    return _text(rendered, "canonical plan proposal", max_length=16_000)


def evaluate_plan_goal_preflight(packet: Mapping[str, Any],
                                 plan: Mapping[str, Any]) -> dict[str, Any]:
    """Bind a goal assessment to precisely the plan the Commander will create."""
    # The existing Commander normalizer is authoritative about what a plan
    # actually persists. Never certify an input that contains design-critical
    # fields which Commander would silently discard (e.g. task.description).
    from scripts.devfarm_errors import DevFarmError
    from scripts.devfarm_plan_validation import validate_plan

    try:
        normalized = validate_plan(plan)
    except DevFarmError as exc:
        raise GoalAlignmentError("reviewed Commander plan is invalid") from exc
    for field in plan:
        if field not in normalized:
            raise GoalAlignmentError(f"unpersisted reviewed plan field: {field}")
    raw_tasks = plan.get("tasks", [])
    for index, raw in enumerate(raw_tasks):
        if not isinstance(raw, Mapping):
            raise GoalAlignmentError("reviewed Commander plan task must be an object")
        for field in raw:
            if field not in normalized["tasks"][index]:
                raise GoalAlignmentError(
                    f"unpersisted reviewed task field at index {index}: {field}"
                )
    result = evaluate_goal_preflight(packet)
    actual = canonical_plan_proposal(plan)
    reported = _text(_object(packet, "packet").get("proposal"),
                     "assessment proposal", max_length=16_000)
    if reported != actual:
        raise GoalAlignmentError(
            "goal assessment is for a different plan; reassess current full plan"
        )
    # ALIGNED is still a trusted-reviewer *advisory*, not a security approval.
    return result


def main(argv: list[str] | None = None) -> int:
    """Local development CLI: printable redacted summary, never source body."""
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path, help="local trusted assessment packet JSON")
    args = parser.parse_args(argv)
    try:
        result = evaluate_goal_preflight(json.loads(args.packet.read_text(encoding="utf-8")))
    except (GoalAlignmentError, ValueError, OSError) as exc:
        parser.exit(2, f"goal-preflight: invalid or insufficient input: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["disposition"] == "ALIGNED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
