"""Command-line adapter for the bounded DevFarm Supervisor.

The Supervisor class owns plan state, review, rework, and integration
authority.  This module owns only argument parsing and the operator-facing
dispatch between those public methods.  It intentionally adds no scheduler,
retry policy, approval authority, or Git mutation of its own.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from scripts.devfarm_plan_state import CommanderPlanStore
from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_repository import read_bounded_json
from scripts.devfarm_supervisor import (
    CodexSupervisedCommanderRun,
    cli_artifact_reference,
    latest_rework_decision,
    providers_for_resume,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="bounded Codex supervisor view over a Commander plan")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "resume", "run", "review", "rework", "integrate", "ownership", "codexless"):
        item = sub.add_parser(command)
        item.add_argument("run_id")
        item.add_argument("--root", type=Path, default=Path.cwd())

    resume = sub.choices["resume"]
    resume.add_argument("--expected-remaining-seconds", type=float)
    resume.add_argument("--timeout-seconds", type=float, default=30.0)
    resume.add_argument("--trust-level", choices=("STATIC_ONLY", "TRUSTED_HOST_EXEC", "OS_SANDBOXED"), default="STATIC_ONLY")
    resume.add_argument("--operator-approved", action="store_true")
    resume.add_argument(
        "--execution-boundary",
        choices=("host_process", "in_process"),
        default="host_process",
        help="where the concrete Worker Provider call runs; live operation defaults to the Host process",
    )

    run = sub.choices["run"]
    run.add_argument("--expected-remaining-seconds", type=float)
    run.add_argument("--timeout-seconds", type=float, default=30.0)
    run.add_argument("--dispatch-timeout-seconds", type=float, default=300.0)
    run.add_argument("--max-wait-seconds", type=float, default=900.0)
    run.add_argument("--trust-level", choices=("STATIC_ONLY", "TRUSTED_HOST_EXEC", "OS_SANDBOXED"), default="STATIC_ONLY")
    run.add_argument("--operator-approved", action="store_true")
    run.add_argument(
        "--execution-boundary",
        choices=("host_process", "in_process"),
        default="host_process",
        help="where the concrete Worker Provider call runs; live operation defaults to the Host process",
    )

    review = sub.choices["review"]
    review.add_argument("task_id")
    review.add_argument("--attempt-id", required=True)
    review.add_argument(
        "--decision",
        choices=("APPROVE_INTEGRATION", "REWORK", "REJECT", "ESCALATE"),
        required=True,
    )
    review.add_argument("--finding", action="append", default=[])
    review.add_argument("--evidence-ref", action="append", default=[])
    review.add_argument("--required-correction")
    review.add_argument("--reviewer-role", default="codex_supervisor")

    rework = sub.choices["rework"]
    rework.add_argument("task_id")
    rework.add_argument("--failure-evidence-ref", required=True)
    rework.add_argument("--review-findings-ref")
    rework.add_argument("--required-correction")
    rework.add_argument("--provider")
    rework.add_argument("--provider-binding-id")
    rework.add_argument("--model")

    integrate = sub.choices["integrate"]
    integrate.add_argument("task_id")
    integrate.add_argument("--decision-id", required=True)
    integrate.add_argument("--target-checkout", type=Path, required=True)
    integrate.add_argument("--target-ref", required=True)
    integrate.add_argument("--commit-message", required=True)

    ownership = sub.choices["ownership"]
    ownership.add_argument(
        "--all",
        action="store_true",
        help="show active ownership across all unfinished Commander plans",
    )

    codexless = sub.choices["codexless"]
    codexless.add_argument("task_id")
    codexless.add_argument("--proposal-file", type=Path, required=True)
    codexless.add_argument("--shadow-evidence-file", type=Path, action="append", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runner = CodexSupervisedCommanderRun(args.root, args.run_id)
    if args.command == "status":
        print(json.dumps(runner.status().to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "ownership":
        records = CommanderPlanStore(args.root).active_ownership(
            run_id=None if args.all else args.run_id
        )
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0
    if args.command == "review":
        step = runner.record_review_decision(
            args.task_id,
            attempt_id=args.attempt_id,
            decision=args.decision,
            findings=args.finding,
            evidence_refs=[cli_artifact_reference(item) for item in args.evidence_ref],
            required_correction=args.required_correction,
            reviewer_role=args.reviewer_role,
        )
        print(json.dumps(step.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "rework":
        plan = runner.plan()
        task = next((item for item in plan["tasks"] if item["task_id"] == args.task_id), None)
        if task is None:
            raise DevFarmError(f"Commander task does not exist: {args.task_id}")
        attempt_id = task.get("last_attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise DevFarmError("rework requires the current worker attempt")
        decision = latest_rework_decision(plan, args.task_id, attempt_id)
        correction = decision.get("required_correction")
        if not isinstance(correction, str) or not correction.strip():
            raise DevFarmError("durable REWORK decision has no required correction")
        if args.required_correction is not None and args.required_correction != correction:
            raise DevFarmError("required correction does not match the durable REWORK decision")
        assignment = task.get("assignment")
        if not isinstance(assignment, Mapping):
            raise DevFarmError("worker task has no assignment")
        provider_id = args.provider or assignment.get("provider_id")
        model_id = args.model or assignment.get("model_id")
        if not isinstance(provider_id, str) or not provider_id.strip() or not isinstance(model_id, str) or not model_id.strip():
            raise DevFarmError("rework requires a provider and model assignment")
        binding_id = args.provider_binding_id or assignment.get("provider_binding_id")
        handoff = runner.rework_handoff(
            args.task_id,
            failure_evidence_reference=cli_artifact_reference(args.failure_evidence_ref, kind="failure_evidence"),
            review_findings_reference=(
                cli_artifact_reference(args.review_findings_ref, kind="review_findings")
                if args.review_findings_ref
                else None
            ),
            required_correction=correction,
        )
        step = runner.reassign(
            args.task_id,
            provider_id=provider_id,
            model_id=model_id,
            provider_binding_id=binding_id,
            rework_handoff=handoff,
        )
        print(json.dumps(step.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "integrate":
        step = runner.integrate_approved_worker(
            args.task_id,
            decision_id=args.decision_id,
            commit_message=args.commit_message,
            target_checkout=args.target_checkout,
            target_ref=args.target_ref,
        )
        print(json.dumps(step.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "codexless":
        proposal = read_bounded_json(args.root, args.proposal_file)
        shadow_evidence = [
            read_bounded_json(args.root, path)
            for path in args.shadow_evidence_file
        ]
        result = runner.evaluate_codexless_candidate(
            args.task_id,
            proposal=proposal,
            shadow_evidence=shadow_evidence,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0 if result.eligible else 2

    providers = providers_for_resume(args.root, args.run_id, args.timeout_seconds)
    step = (
        runner.advance(
            providers=providers,
            expected_remaining_seconds=args.expected_remaining_seconds,
            verification_trust_level=args.trust_level,
            operator_approved=args.operator_approved,
            execution_boundary=args.execution_boundary,
        )
        if args.command == "resume"
        else runner.run_until_intervention(
            providers=providers,
            expected_remaining_seconds=args.expected_remaining_seconds,
            verification_trust_level=args.trust_level,
            operator_approved=args.operator_approved,
            dispatch_timeout_seconds=args.dispatch_timeout_seconds,
            max_wait_seconds=args.max_wait_seconds,
            execution_boundary=args.execution_boundary,
        )
    )
    print(json.dumps(step.to_dict(), ensure_ascii=False, indent=2))
    return 0


__all__ = ["main"]
