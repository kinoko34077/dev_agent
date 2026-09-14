"""Thin Host composition for an explicitly approved D9 repair candidate.

This module does not create a repair scheduler or a second integration path.
It rechecks the candidate against the existing Supervisor plan, consumes the
existing durable external-write approval only after those checks pass, and
then delegates Git mutation to ``integrate_approved_worker``.  It never
retries, rolls back, or infers an unknown external outcome.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from scripts.devfarm import DevFarmError
from src.dev_agent.intelligence.self_repair import (
    RepairCandidate,
    RepairExecutionPolicy,
    RepairExecutionRequest,
)
from src.dev_agent.policy.approvals import ApprovalPolicy


def _task(plan: Mapping[str, Any], task_id: str) -> Mapping[str, Any]:
    tasks = plan.get("tasks")
    if not isinstance(tasks, list):
        raise DevFarmError("repair plan tasks are missing")
    for item in tasks:
        if isinstance(item, Mapping) and item.get("task_id") == task_id:
            return item
    raise DevFarmError(f"repair task does not exist: {task_id}")


def _review_decision(plan: Mapping[str, Any], request: RepairExecutionRequest) -> Mapping[str, Any]:
    decisions = plan.get("review_decisions")
    if not isinstance(decisions, list):
        raise DevFarmError("repair plan review decisions are missing")
    for item in decisions:
        if not isinstance(item, Mapping):
            continue
        if (
            item.get("decision_id") == request.review_decision_id
            and item.get("task_id") == request.task_id
            and item.get("attempt_id") == request.attempt_id
        ):
            if item.get("decision") != "APPROVE_INTEGRATION":
                raise DevFarmError("repair requires APPROVE_INTEGRATION")
            return item
    raise DevFarmError("matching durable repair review decision is missing")


def _artifact_paths(packet: Mapping[str, Any]) -> set[str]:
    references = packet.get("artifact_refs")
    if not isinstance(references, list):
        raise DevFarmError("repair ReviewPacket artifact references are missing")
    paths: set[str] = set()
    for reference in references:
        if isinstance(reference, Mapping) and isinstance(reference.get("path"), str):
            paths.add(reference["path"])
    return paths


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
    task = _task(plan, request.task_id)
    if task.get("owner") != "worker" or task.get("status") != "HOST_VERIFIED":
        raise DevFarmError("repair requires a HOST_VERIFIED Worker task")
    if task.get("last_attempt_id") != request.attempt_id:
        raise DevFarmError("repair attempt does not match the current Worker attempt")
    if task.get("verified_patch_digest") != candidate.evidence.patch_sha256:
        raise DevFarmError("repair patch digest does not match the current task")
    if task.get("manifest_path") != candidate.evidence.manifest_ref:
        raise DevFarmError("repair manifest reference does not match the current task")
    _review_decision(plan, request)

    packet = runner.review_packet(request.task_id, attempt_id=request.attempt_id)
    if packet.get("patch_sha256") != candidate.evidence.patch_sha256:
        raise DevFarmError("repair ReviewPacket patch digest does not match candidate")
    references = _artifact_paths(packet)
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


__all__ = ["integrate_approved_repair"]
