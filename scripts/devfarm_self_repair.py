"""Thin Host composition for an explicitly approved D9 repair candidate.

This module does not create a repair scheduler or a second integration path.
It rechecks the candidate against the existing Supervisor plan, consumes the
existing durable external-write approval only after those checks pass, and
then delegates Git mutation to ``integrate_approved_worker``.  It never
retries, rolls back, or infers an unknown external outcome.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_manifests import load_worker_manifest
from scripts.devfarm_plan_queries import (
    artifact_reference_paths,
    require_approved_review_decision,
    require_task,
)
from src.dev_agent.intelligence.self_improvement import ImprovementPlanProposal
from src.dev_agent.intelligence.self_repair import (
    RepairCandidate,
    RepairExecutionPolicy,
    RepairExecutionRequest,
    RepairEvidence,
    RepairEvaluation,
    RepairPolicy,
    RollbackProof,
)
from src.dev_agent.policy.approvals import ApprovalPolicy


def build_repair_candidate(
    runner: Any,
    improvement_plan: ImprovementPlanProposal,
    task_id: str,
    *,
    rollback_ref: str,
    rollback_proof: RollbackProof | None = None,
    external_outcome_known: bool,
) -> RepairEvaluation:
    """Build one proposal-only repair candidate from current Host evidence.

    This is a read-only composition boundary.  It rehydrates the current
    Supervisor task, validated Worker manifest, and compact ReviewPacket, then
    delegates eligibility to ``RepairPolicy``.  It does not read raw Worker
    output, consume approval, mutate a Task, or touch Git.
    """

    if not isinstance(improvement_plan, ImprovementPlanProposal):
        raise TypeError("improvement_plan must be an ImprovementPlanProposal")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-empty string")
    if not isinstance(external_outcome_known, bool):
        raise TypeError("external_outcome_known must be a boolean")
    root = getattr(runner, "root", None)
    if not isinstance(root, (str, Path)):
        raise TypeError("runner must expose a repository root")
    if not callable(getattr(runner, "plan", None)) or not callable(
        getattr(runner, "review_packet", None)
    ):
        raise TypeError("runner must expose public plan() and review_packet()")

    plan = runner.plan()
    task = require_task(plan, task_id)
    if task.get("owner") != "worker" or task.get("status") != "HOST_VERIFIED":
        raise DevFarmError("repair candidate requires a HOST_VERIFIED Worker task")
    attempt_id = task.get("last_attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise DevFarmError("repair candidate requires a current Worker attempt")
    packet = runner.review_packet(task_id, attempt_id=attempt_id)
    if packet.get("task_id") != task_id or packet.get("attempt_id") != attempt_id:
        raise DevFarmError("repair ReviewPacket identity does not match the current task")
    if packet.get("status") != "HOST_VERIFIED":
        raise DevFarmError("repair candidate requires a Host-verified ReviewPacket")

    summary = packet.get("verification_summary")
    if not isinstance(summary, Mapping):
        raise DevFarmError("repair ReviewPacket verification summary is missing")
    patch_sha256 = packet.get("patch_sha256")
    changed_files = packet.get("changed_files")
    verification_ref = packet.get("verification_ref")
    if not isinstance(patch_sha256, str) or not patch_sha256.strip():
        raise DevFarmError("repair ReviewPacket patch digest is missing")
    if not isinstance(changed_files, Sequence) or isinstance(changed_files, (str, bytes)):
        raise DevFarmError("repair ReviewPacket changed files are missing")
    if not isinstance(verification_ref, str) or not verification_ref.strip():
        raise DevFarmError("repair ReviewPacket verification reference is missing")
    references = packet.get("artifact_refs")
    if not isinstance(references, list):
        raise DevFarmError("repair ReviewPacket artifact references are missing")
    patch_ref = next(
        (
            item.get("path")
            for item in references
            if isinstance(item, Mapping)
            and item.get("kind") == "patch"
            and isinstance(item.get("path"), str)
        ),
        None,
    )
    if not isinstance(patch_ref, str) or not patch_ref.strip():
        raise DevFarmError("repair ReviewPacket patch reference is missing")
    reference_paths = artifact_reference_paths(packet)
    if patch_ref not in reference_paths or verification_ref not in reference_paths:
        raise DevFarmError("repair ReviewPacket artifact references are incomplete")
    if task.get("verified_patch_digest") != patch_sha256:
        raise DevFarmError("repair task patch digest does not match the ReviewPacket")

    _, manifest = load_worker_manifest(Path(root).resolve(), task)
    trust_level = summary.get("verification_trust_level")
    operator_approved = summary.get("operator_approved")
    independent_verification = summary.get("independent_verification")
    if (
        not isinstance(trust_level, str)
        or not isinstance(operator_approved, bool)
        or not isinstance(independent_verification, bool)
    ):
        raise DevFarmError("repair verification summary is incomplete")
    verification_passed = all(
        summary.get(key) is True
        for key in ("host_verified", "host_tests_passed", "result_accepted")
    )
    evidence = RepairEvidence(
        plan_id=improvement_plan.plan_id,
        base_revision=manifest["base_revision"],
        attempt_id=attempt_id,
        patch_ref=patch_ref,
        patch_sha256=patch_sha256,
        manifest_ref=str(task["manifest_path"]),
        verification_ref=verification_ref,
        changed_files=tuple(changed_files),
        verification_status="passed" if verification_passed else "failed",
        verification_trust_level=trust_level,
        operator_approved=operator_approved,
        independent_verification=independent_verification,
        external_outcome_known=external_outcome_known,
        rollback_ref=rollback_ref,
        rollback_proof=rollback_proof,
    )
    return RepairPolicy().evaluate(improvement_plan, evidence)


def integrate_approved_repair(
    runner: Any,
    candidate: RepairCandidate,
    request: RepairExecutionRequest,
    *,
    approval_store: Any,
    target_checkout: str | Path,
) -> Any:
    """Use existing Host integration for one explicitly approved candidate.

    All checks happen before approval consumption.  The approval is bound to
    the exact request arguments by ``RepairExecutionPolicy``.  If the Host
    helper raises after consumption, callers must reconcile and obtain a new
    explicit approval; this adapter never retries or guesses the outcome.
    """

    if not isinstance(getattr(runner, "run_id", None), str):
        raise TypeError("runner must expose a Supervisor run_id")
    for method_name in ("plan", "review_packet", "integrate_approved_worker"):
        if not callable(getattr(runner, method_name, None)):
            raise TypeError(f"runner must expose public {method_name}()")
    evaluation = RepairExecutionPolicy().evaluate(candidate, request, approval_store)
    if not evaluation.eligible:
        raise DevFarmError(
            "repair execution preflight rejected: " + ",".join(evaluation.reasons)
        )
    if runner.run_id != request.run_id:
        raise DevFarmError("repair request run identity does not match Supervisor")
    target_path = Path(target_checkout).resolve()
    approved_target = Path(request.target_checkout_ref).resolve()
    if target_path != approved_target:
        raise DevFarmError("repair target checkout does not match the approved reference")

    plan = runner.plan()
    task = require_task(plan, request.task_id)
    if task.get("owner") != "worker" or task.get("status") != "HOST_VERIFIED":
        raise DevFarmError("repair requires a HOST_VERIFIED Worker task")
    if task.get("last_attempt_id") != request.attempt_id:
        raise DevFarmError("repair attempt does not match the current Worker attempt")
    if task.get("verified_patch_digest") != candidate.evidence.patch_sha256:
        raise DevFarmError("repair patch digest does not match the current task")
    if task.get("manifest_path") != candidate.evidence.manifest_ref:
        raise DevFarmError("repair manifest reference does not match the current task")
    require_approved_review_decision(
        plan,
        decision_id=request.review_decision_id,
        task_id=request.task_id,
        attempt_id=request.attempt_id,
    )

    packet = runner.review_packet(request.task_id, attempt_id=request.attempt_id)
    if packet.get("patch_sha256") != candidate.evidence.patch_sha256:
        raise DevFarmError("repair ReviewPacket patch digest does not match candidate")
    references = artifact_reference_paths(packet)
    if candidate.evidence.patch_ref not in references:
        raise DevFarmError("repair patch reference is absent from the ReviewPacket")
    if candidate.evidence.verification_ref not in references:
        raise DevFarmError("repair verification reference is absent from the ReviewPacket")

    consumed = ApprovalPolicy().authorize(
        "external_write",
        approval_id=request.approval_id,
        task_id=request.task_id,
        call_id=request.call_id,
        arguments_hash=request.authorization_hash(),
        store=approval_store,
    )
    if consumed is not True:
        raise DevFarmError("repair approval could not be consumed")

    return runner.integrate_approved_worker(
        request.task_id,
        decision_id=request.review_decision_id,
        commit_message=request.commit_message,
        target_checkout=target_checkout,
        target_ref=request.target_ref,
    )


__all__ = ["build_repair_candidate", "integrate_approved_repair"]
