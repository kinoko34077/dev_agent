"""Bounded Codex-supervised composition over the existing Commander primitives.

``advance`` remains a single bounded pass for callers that need a snapshot;
``run_until_intervention`` provides the blocking, no-LLM-polling supervisor
entrypoint.  The supervisor never starts a background scheduler,
auto-integrates, or treats Worker output as authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping, Sequence
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_artifacts import artifact_reference
from scripts.devfarm_integration import (
    integrate_approved_worker as host_integrate_approved_worker,
    integrate_worker as host_record_integration,
)
from scripts.devfarm_manifests import load_worker_manifest
from scripts.devfarm_repository import read_bounded_json
from scripts.devfarm_review_packet import build_review_packet
from scripts.devfarm_commander import (
    CommanderPlanStore,
    record_result,
    collect_plan,
    dispatch_plan,
    recover_orphaned_dispatches,
    reassign_task,
    refresh_plan,
    verify_plan,
    summarize_delegation,
)
from scripts.devfarm_plan_queries import latest_rework_decision as query_latest_rework_decision
from scripts.devfarm_resume import providers_for_resume as compose_providers_for_resume
from scripts.devfarm_review_protocol import normalize_review_decision, normalize_review_packet
from scripts.devfarm_supervisor_protocol import (
    advance_heartbeat,
    normalize_supervisor_metadata,
    record_wake,
    select_heartbeat_cadence,
)
from src.dev_agent.handoff import ExternalTextReference, HandoffEnvelope, rework_request
from src.dev_agent.intelligence.codexless import CodexLessEvaluation, CodexLessPolicy


def _remaining_supervisor_deadline(metadata: Mapping[str, Any]) -> float | None:
    value = metadata.get("overall_deadline")
    if value is None:
        return None
    if not isinstance(value, str):
        raise DevFarmError("supervisor overall deadline is invalid")
    try:
        deadline = datetime.fromisoformat(value)
    except ValueError as exc:
        raise DevFarmError("supervisor overall deadline is invalid") from exc
    if deadline.tzinfo is None:
        raise DevFarmError("supervisor overall deadline must include a timezone")
    return (deadline - datetime.now(timezone.utc)).total_seconds()


def _requires_codex_action(plan: Mapping[str, Any], step: "SupervisorStep") -> bool:
    """Return whether the caller, rather than the worker wait loop, must act."""

    if step.status == "REVIEWING":
        return True
    if step.status == "INTEGRATING" and step.next_action == "integrate_verified_worker":
        return True
    if step.status == "ACTIVE" and step.next_action in {
        "execute_codex_task",
        "rework_worker",
        "reassign_worker",
        "resolve_rejection",
    }:
        return True
    return False


@dataclass(frozen=True)
class SupervisorStep:
    run_id: str
    plan_revision: int
    plan_status: str
    status: str
    cadence_minutes: int
    unchanged_check_count: int
    next_action: str
    wake_events: tuple[Mapping[str, Any], ...]
    review_packets: tuple[Mapping[str, Any], ...]
    metrics: Mapping[str, int]
    delegation: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "plan_revision": self.plan_revision,
            "plan_status": self.plan_status,
            "status": self.status,
            "cadence_minutes": self.cadence_minutes,
            "unchanged_check_count": self.unchanged_check_count,
            "next_action": self.next_action,
            "wake_events": [dict(item) for item in self.wake_events],
            "review_packets": [dict(item) for item in self.review_packets],
            "metrics": dict(self.metrics),
            "delegation": dict(self.delegation),
        }


class CodexSupervisedCommanderRun:
    """Manage one bounded supervisor pass over a durable Commander plan."""

    def __init__(self, root: str | Path, run_id: str) -> None:
        self.root = Path(root).resolve()
        self.run_id = run_id
        self.store = CommanderPlanStore(self.root)

    def plan(self) -> dict[str, Any]:
        return self.store.load(self.run_id)

    def ownership(self) -> list[dict[str, Any]]:
        """Return active file ownership for this run without changing state."""

        return self.store.active_ownership(run_id=self.run_id)

    def _step(self, plan: Mapping[str, Any]) -> SupervisorStep:
        metadata = normalize_supervisor_metadata(plan.get("supervisor"))
        return SupervisorStep(
            run_id=plan["run_id"],
            plan_revision=plan["plan_revision"],
            plan_status=plan["status"],
            status=metadata["status"],
            cadence_minutes=metadata["cadence_minutes"],
            unchanged_check_count=metadata["unchanged_check_count"],
            next_action=metadata["next_action"],
            wake_events=tuple(metadata["wake_events"]),
            review_packets=tuple(metadata["review_packets"]),
            metrics=metadata["metrics"],
            delegation=summarize_delegation(plan),
        )

    def _review_packet(self, task: Mapping[str, Any]) -> dict[str, Any]:
        """Build the compact packet through the shared read-only boundary."""

        return build_review_packet(self.root, task)

    def review_packet(self, task_id: str, *, attempt_id: str | None = None) -> dict[str, Any]:
        """Return one current compact packet through the Supervisor boundary."""

        task = next((item for item in self.plan()["tasks"] if item["task_id"] == task_id), None)
        if task is None:
            raise DevFarmError(f"Commander task does not exist: {task_id}")
        current_attempt = task.get("last_attempt_id")
        if attempt_id is not None and attempt_id != current_attempt:
            raise DevFarmError("review packet attempt does not match the current worker attempt")
        return self._review_packet(task)

    def evaluate_codexless_candidate(
        self,
        task_id: str,
        *,
        proposal: Any,
        shadow_evidence: Sequence[Mapping[str, Any]],
        packet: Mapping[str, Any] | None = None,
    ) -> CodexLessEvaluation:
        """Evaluate a routine D7 candidate without granting integration authority."""

        plan = self.plan()
        task = next((item for item in plan["tasks"] if item["task_id"] == task_id), None)
        if task is None:
            raise DevFarmError(f"Commander task does not exist: {task_id}")
        _, manifest = load_worker_manifest(self.root, task)
        current_packet = dict(packet) if packet is not None else self.review_packet(task_id)
        return CodexLessPolicy().evaluate(
            task=task,
            manifest=manifest,
            packet=current_packet,
            proposal=proposal,
            shadow_evidence=shadow_evidence,
        )

    def record_review_decision(
        self,
        task_id: str,
        *,
        attempt_id: str,
        decision: str,
        findings: tuple[str, ...] | list[str] = (),
        evidence_refs: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
        required_correction: str | None = None,
        reviewer_role: str = "reviewer",
    ) -> SupervisorStep:
        """Persist one explicit review decision through the plan CAS."""

        plan = self.store.load(self.run_id)
        task = next((item for item in plan["tasks"] if item["task_id"] == task_id), None)
        if task is None:
            raise DevFarmError(f"Commander task does not exist: {task_id}")
        normalized = normalize_review_decision(
            {
                "decision_id": f"review-{uuid4().hex}",
                "task_id": task_id,
                "attempt_id": attempt_id,
                "decision": decision,
                "findings": list(findings),
                "evidence_refs": list(evidence_refs),
                "required_correction": required_correction,
                "reviewer_role": reviewer_role,
                "decided_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if normalized["decision"] in {"APPROVE_INTEGRATION", "REWORK", "REJECT"}:
            if task.get("last_attempt_id") != attempt_id:
                raise DevFarmError("review decision must match the current worker attempt")
            if task.get("status") != "HOST_VERIFIED":
                raise DevFarmError("worker review decision requires a HOST_VERIFIED task")
            if any(
                item.get("task_id") == task_id and item.get("attempt_id") == attempt_id
                for item in plan["review_decisions"]
            ):
                raise DevFarmError("review decision already exists for the current worker attempt")
        if normalized["decision"] == "REWORK":
            # Make the explicit review decision actionable.  Reassigning this
            # task then creates a new immutable manifest carrying the rework
            # delta; the old verified attempt remains historical evidence.
            if not normalized.get("required_correction"):
                raise DevFarmError("REWORK requires required_correction")
            task["status"] = "REJECTED"
            task["block_reason"] = "review_rework_required"
            correction = normalized.get("required_correction")
            task["last_error"] = correction
            record_result(
                plan,
                task_id,
                "review",
                "rework",
                task.get("result_ref"),
                attempt_id=attempt_id,
            )
        elif normalized["decision"] == "REJECT":
            task["status"] = "REJECTED"
            task["block_reason"] = "review_rejected"
            record_result(
                plan,
                task_id,
                "review",
                "rejected",
                task.get("result_ref"),
                attempt_id=attempt_id,
            )
        plan["review_decisions"].append(normalized)
        # Keep the Plan projection consistent with the task terminal change;
        # callers should not need a separate refresh just to observe REJECTED
        # after an explicit review decision.
        plan = refresh_plan(plan)
        saved = self.store.save(plan, expected_revision=plan["plan_revision"])
        metadata = normalize_supervisor_metadata(saved.get("supervisor"))
        metadata["metrics"]["codex_review_count"] += 1
        metadata["metrics"]["codex_review_decision_count"] += 1
        if normalized["decision"] == "APPROVE_INTEGRATION":
            metadata["status"] = "INTEGRATING"
            metadata["next_action"] = "integrate_verified_worker"
        elif normalized["decision"] == "REWORK":
            metadata["status"] = "ACTIVE"
            metadata["next_action"] = "rework_worker"
        elif normalized["decision"] == "REJECT":
            metadata["status"] = "BLOCKED"
            metadata["next_action"] = "resolve_rejection"
        else:
            metadata["status"] = "HUMAN_DECISION_REQUIRED"
            metadata["next_action"] = "resolve_escalation"
        return self._step(self._save_supervisor(metadata))

    def _save_supervisor(self, metadata: Mapping[str, Any]) -> dict[str, Any]:
        current = self.store.load(self.run_id)
        current["supervisor"] = normalize_supervisor_metadata(metadata)
        return self.store.save(current, expected_revision=current["plan_revision"])

    def create(
        self,
        *,
        roadmap_reference: Mapping[str, Any] | None = None,
        expected_remaining_seconds: int | float | None = None,
        unchanged_check_limit: int = 2,
        overall_deadline: str | None = None,
    ) -> SupervisorStep:
        plan = self.store.load(self.run_id)
        metadata = normalize_supervisor_metadata(
            {
                "status": "ACTIVE",
                "roadmap_reference": dict(roadmap_reference or {}),
                "cadence_minutes": select_heartbeat_cadence(expected_remaining_seconds),
                "unchanged_check_limit": unchanged_check_limit,
                "overall_deadline": overall_deadline,
                "next_action": "advance",
            }
        )
        saved = dict(plan)
        saved["supervisor"] = metadata
        saved = self.store.save(saved, expected_revision=plan["plan_revision"])
        return self._step(saved)

    def status(self) -> SupervisorStep:
        return self._step(self.store.load(self.run_id))

    def advance(
        self,
        *,
        providers: Mapping[str, Any],
        orchestrator: Any | None = None,
        expected_remaining_seconds: int | float | None = None,
        verification_trust_level: str = "STATIC_ONLY",
        operator_approved: bool = False,
        dispatch_timeout_seconds: int | float = 300.0,
        execution_boundary: str = "in_process",
    ) -> SupervisorStep:
        """Run exactly one bounded refresh/dispatch/collect/verify pass."""

        # Reconcile durable result artifacts before classifying an old
        # dispatch.  An expired dispatch with a result must be collected, not
        # blindly retried; a result-less expired dispatch is blocked by the
        # Commander helper and requires an explicit recovery decision.
        collect_plan(self.root, self.run_id)
        recover_orphaned_dispatches(self.root, self.run_id)
        initial = refresh_plan(self.store.load(self.run_id))
        initial_metadata = normalize_supervisor_metadata(initial.get("supervisor"))
        initial["supervisor"] = initial_metadata
        initial = self.store.save(initial, expected_revision=initial["plan_revision"])
        ready_worker_ids = [
            task["task_id"]
            for task in initial["tasks"]
            if task["owner"] == "worker" and task["status"] == "READY"
        ]
        if ready_worker_ids:
            try:
                host_dispatches = None
                if execution_boundary == "host_process":
                    from scripts.devfarm_host_dispatch import create_host_process_executor
                    from src.dev_agent.providers.host_dispatch import HostProviderDispatch

                    executor = create_host_process_executor(
                        self.root / ".devfarm" / "host-dispatch",
                        timeout_seconds=dispatch_timeout_seconds,
                    )
                    host_dispatches = {
                        task_id: HostProviderDispatch(
                            providers[task_id],
                            execution_boundary="host_process",
                            executor=executor,
                        )
                        for task_id in ready_worker_ids
                        if task_id in providers
                    }
                elif execution_boundary != "in_process":
                    raise DevFarmError("execution_boundary must be in_process or host_process")
                dispatch_plan(
                    self.root,
                    self.run_id,
                    providers=providers,
                    host_dispatches=host_dispatches,
                    orchestrator=orchestrator,
                    dispatch_timeout_seconds=dispatch_timeout_seconds,
                )
            except (DevFarmError, TypeError, ValueError) as exc:
                metadata = normalize_supervisor_metadata(self.store.load(self.run_id).get("supervisor"))
                metadata["status"] = "ACTIVE"
                metadata["next_action"] = "reassign_worker"
                metadata = record_wake(metadata, kind="NO_ELIGIBLE_WORKER", digest=None)
                return self._step(self._save_supervisor(metadata))

        collect_plan(self.root, self.run_id)
        proposed = [
            task["task_id"]
            for task in self.store.load(self.run_id)["tasks"]
            if task["owner"] == "worker" and task["status"] == "PROPOSED"
        ]
        if proposed:
            verify_plan(
                self.root,
                self.run_id,
                task_ids=proposed,
                orchestrator=orchestrator,
                verification_trust_level=verification_trust_level,
                operator_approved=operator_approved,
            )

        plan = refresh_plan(self.store.load(self.run_id))
        metadata = normalize_supervisor_metadata(plan.get("supervisor"))
        previous_events = {
            tuple(item.get(key) for key in ("kind", "task_id", "attempt_id", "digest"))
            for item in metadata["wake_events"]
        }
        newly_verified = 0
        new_review_requests = 0
        packet_identities = {
            (item.get("task_id"), item.get("attempt_id"))
            for item in metadata["review_packets"]
        }
        review_decisions = {
            (item.get("task_id"), item.get("attempt_id")): item
            for item in plan["review_decisions"]
        }
        for task in plan["tasks"]:
            if task["status"] == "HOST_VERIFIED":
                review_decision = review_decisions.get((task.get("task_id"), task.get("last_attempt_id")))
                if review_decision is not None:
                    # A durable decision is already waiting for its next
                    # Host-side action.  Do not emit another review request
                    # after a resume or a status refresh.
                    continue
                before = len(metadata["wake_events"])
                metadata = record_wake(
                    metadata,
                    kind="HOST_VERIFIED_RESULT_READY",
                    task_id=task["task_id"],
                    attempt_id=task.get("last_attempt_id"),
                    digest=task.get("verified_patch_digest"),
                )
                if len(metadata["wake_events"]) > before:
                    newly_verified += 1
                    new_review_requests += 1
                identity = (task.get("task_id"), task.get("last_attempt_id"))
                if identity not in packet_identities:
                    metadata["review_packets"].append(self._review_packet(task))
                    packet_identities.add(identity)
                else:
                    # Rehydrate packets created by an older Supervisor
                    # version so a durable plan cannot retain proposal-time
                    # verification flags after the append-only record exists.
                    for index, packet in enumerate(metadata["review_packets"]):
                        if (packet.get("task_id"), packet.get("attempt_id")) == identity:
                            refreshed = self._review_packet(task)
                            if packet != refreshed:
                                metadata["review_packets"][index] = refreshed
                            break
            elif task["status"] == "REJECTED" and task.get("last_attempt_id"):
                metadata = record_wake(
                    metadata,
                    kind="WORKER_REJECTED_AFTER_RETRY",
                    task_id=task["task_id"],
                    attempt_id=task.get("last_attempt_id"),
                )
        current_events = {
            tuple(item.get(key) for key in ("kind", "task_id", "attempt_id", "digest"))
            for item in metadata["wake_events"]
        }
        new_wakes = len(current_events - previous_events)
        metadata["metrics"]["codex_wake_count"] += new_wakes
        metadata["metrics"]["codex_review_request_count"] += new_review_requests
        metadata["metrics"]["worker_success_count"] += newly_verified
        metadata["metrics"]["worker_dispatch_count"] += len(ready_worker_ids)

        if all(task["status"] == "INTEGRATED" for task in plan["tasks"]):
            metadata["status"] = "COMPLETED"
            metadata["next_action"] = "stop"
        elif any(
            item.get("decision") == "ESCALATE"
            and any(
                task.get("task_id") == item.get("task_id")
                and task.get("last_attempt_id") == item.get("attempt_id")
                for task in plan["tasks"]
            )
            for item in plan["review_decisions"]
        ):
            metadata["status"] = "HUMAN_DECISION_REQUIRED"
            metadata["next_action"] = "resolve_escalation"
        elif any(
            item.get("decision") == "REWORK"
            and any(
                task.get("task_id") == item.get("task_id")
                and task.get("last_attempt_id") == item.get("attempt_id")
                for task in plan["tasks"]
            )
            for item in plan["review_decisions"]
        ):
            metadata["status"] = "ACTIVE"
            metadata["next_action"] = "rework_worker"
        elif any(
            item.get("decision") == "APPROVE_INTEGRATION"
            and any(
                task.get("task_id") == item.get("task_id")
                and task.get("last_attempt_id") == item.get("attempt_id")
                and task.get("status") != "INTEGRATED"
                for task in plan["tasks"]
            )
            for item in plan["review_decisions"]
        ):
            metadata["status"] = "INTEGRATING"
            metadata["next_action"] = "integrate_verified_worker"
        elif any(task["status"] == "HOST_VERIFIED" for task in plan["tasks"]):
            metadata["status"] = "REVIEWING"
            metadata["next_action"] = "review_host_verified"
        elif any(task["status"] in {"DISPATCHED", "PROPOSED"} for task in plan["tasks"]):
            metadata["status"] = "WAITING_FOR_WORKER"
            metadata["next_action"] = "wait_for_worker"
        elif any(
            task.get("owner") == "codex"
            and task.get("status") in {"PLANNED", "READY", "ACTIVE"}
            for task in plan["tasks"]
        ):
            metadata["status"] = "ACTIVE"
            metadata["next_action"] = "execute_codex_task"
        elif plan["status"] in {"BLOCKED", "REJECTED"}:
            metadata["status"] = "BLOCKED"
            metadata["next_action"] = "resolve_blocker"
        else:
            metadata["status"] = "ACTIVE"
            metadata["next_action"] = "advance"

        metadata = advance_heartbeat(
            metadata,
            unchanged=(new_wakes == 0 and not ready_worker_ids),
            expected_remaining_seconds=expected_remaining_seconds,
        )
        return self._step(self._save_supervisor(metadata))

    def run_until_intervention(
        self,
        *,
        providers: Mapping[str, Any],
        orchestrator: Any | None = None,
        expected_remaining_seconds: int | float | None = None,
        verification_trust_level: str = "STATIC_ONLY",
        operator_approved: bool = False,
        dispatch_timeout_seconds: int | float = 300.0,
        max_wait_seconds: int | float = 900.0,
        sleep_fn: Any = time.sleep,
        monotonic_fn: Any = time.monotonic,
        execution_boundary: str = "in_process",
    ) -> SupervisorStep:
        """Keep the lightweight supervisor process alive until intervention.

        Worker/provider execution remains synchronous where the existing
        DevFarm is synchronous.  If a durable plan is already waiting on a
        process that survived outside this call, this method sleeps without
        invoking an LLM and resumes on the persisted cadence.  It returns
        only at a review, terminal, blocker, or bounded-wait boundary.
        """

        if isinstance(max_wait_seconds, bool) or not isinstance(max_wait_seconds, (int, float)):
            raise ValueError("max_wait_seconds must be numeric")
        if max_wait_seconds <= 0:
            raise ValueError("max_wait_seconds must be positive")
        if not callable(sleep_fn) or not callable(monotonic_fn):
            raise TypeError("sleep_fn and monotonic_fn must be callable")
        started = float(monotonic_fn())
        while True:
            current_metadata = normalize_supervisor_metadata(self.store.load(self.run_id).get("supervisor"))
            deadline_remaining = _remaining_supervisor_deadline(current_metadata)
            if deadline_remaining is not None and deadline_remaining <= 0:
                current_metadata["next_action"] = "supervisor_overall_deadline"
                current_metadata = record_wake(current_metadata, kind="SUPERVISOR_OVERALL_DEADLINE")
                return self._step(self._save_supervisor(current_metadata))
            elapsed = max(0.0, float(monotonic_fn()) - started)
            remaining = float(max_wait_seconds) - elapsed
            if remaining <= 0:
                metadata = normalize_supervisor_metadata(self.store.load(self.run_id).get("supervisor"))
                metadata["status"] = "WAITING_FOR_WORKER"
                metadata["next_action"] = "wait_budget_exhausted"
                metadata = record_wake(metadata, kind="SUPERVISOR_WAIT_BUDGET_EXHAUSTED")
                return self._step(self._save_supervisor(metadata))

            step = self.advance(
                providers=providers,
                orchestrator=orchestrator,
                expected_remaining_seconds=expected_remaining_seconds,
                verification_trust_level=verification_trust_level,
                operator_approved=operator_approved,
                dispatch_timeout_seconds=dispatch_timeout_seconds,
                execution_boundary=execution_boundary,
            )
            if step.status in {"HUMAN_DECISION_REQUIRED", "COMPLETED", "BLOCKED"}:
                return step
            if _requires_codex_action(self.store.load(self.run_id), step):
                return step
            if step.status not in {"WAITING_FOR_WORKER", "ACTIVE", "WORKER_RESULT_READY", "INTEGRATING"}:
                return step

            # The cadence is a durable wake hint, not a second scheduler.
            # Sleeping here consumes no Codex reasoning and never replays an
            # external effect by itself.
            sleep_limits = [remaining, max(1.0, step.cadence_minutes * 60.0)]
            if deadline_remaining is not None:
                sleep_limits.append(max(0.0, deadline_remaining))
            sleep_for = min(sleep_limits)
            if sleep_for <= 0:
                continue
            sleep_fn(sleep_for)

    def rework_handoff(
        self,
        task_id: str,
        *,
        failure_evidence_reference: Mapping[str, Any] | ExternalTextReference,
        review_findings_reference: Mapping[str, Any] | ExternalTextReference | None,
        required_correction: str,
    ) -> HandoffEnvelope:
        return rework_request(
            task_reference={"run_id": self.run_id, "task_id": task_id},
            failure_evidence_reference=failure_evidence_reference,
            review_findings_reference=review_findings_reference,
            required_correction=required_correction,
        )

    def reassign(
        self,
        task_id: str,
        *,
        provider_id: str,
        model_id: str,
        provider_binding_id: str | None = None,
        rework_handoff: HandoffEnvelope | Mapping[str, Any] | None = None,
    ) -> SupervisorStep:
        handoff_value: Mapping[str, Any] | None = None
        if rework_handoff is not None:
            if isinstance(rework_handoff, HandoffEnvelope):
                serialized = rework_handoff.to_dict()
                # Keep the durable manifest delta reference-first.  In
                # particular, do not copy the envelope's inline payload.
                serialized.pop("payload", None)
                serialized.pop("metadata", None)
                handoff_value = serialized
            elif isinstance(rework_handoff, Mapping):
                handoff_value = dict(rework_handoff)
            else:
                raise TypeError("rework_handoff must be a HandoffEnvelope, object, or None")
        reassign_task(
            self.root,
            self.run_id,
            task_id,
            provider_id=provider_id,
            model_id=model_id,
            provider_binding_id=provider_binding_id,
            rework_handoff=handoff_value,
        )
        metadata = normalize_supervisor_metadata(self.store.load(self.run_id).get("supervisor"))
        metadata["status"] = "ACTIVE"
        metadata["next_action"] = "advance"
        metadata["metrics"]["worker_retry_count"] += 1
        return self._step(self._save_supervisor(metadata))

    def integrate_approved_worker(
        self,
        task_id: str,
        *,
        decision_id: str,
        commit_message: str,
        target_checkout: str | Path,
        target_ref: str,
    ) -> SupervisorStep:
        """Delegate deterministic Git mutation to the Host integration service."""

        host_integrate_approved_worker(
            self.root,
            self.run_id,
            task_id,
            decision_id=decision_id,
            commit_message=commit_message,
            target_checkout=target_checkout,
            target_ref=target_ref,
        )
        plan = refresh_plan(self.store.load(self.run_id))
        metadata = normalize_supervisor_metadata(plan.get("supervisor"))
        metadata["status"] = "COMPLETED" if plan["status"] == "INTEGRATED" else "ACTIVE"
        metadata["next_action"] = "stop" if metadata["status"] == "COMPLETED" else "advance"
        return self._step(self._save_supervisor(metadata))

    def approve_integration(
        self,
        task_id: str,
        *,
        note: str,
        target_ref: str,
        integration_revision: str,
        source_attempt_id: str,
        verified_patch_digest: str,
    ) -> SupervisorStep:
        """Record approval for a pre-existing explicit Git integration.

        This compatibility path is retained for callers that already
        performed the Git commit.  New callers should use
        ``integrate_approved_worker`` so the Host applies and commits the
        verified patch deterministically after this same durable decision.
        """

        current = self.store.load(self.run_id)
        task = next((item for item in current["tasks"] if item["task_id"] == task_id), None)
        if task is None:
            raise DevFarmError(f"Commander task does not exist: {task_id}")
        if task.get("status") != "HOST_VERIFIED" or task.get("last_attempt_id") != source_attempt_id:
            raise DevFarmError("approval must match the current HOST_VERIFIED attempt")
        if not any(
            item.get("task_id") == task_id
            and item.get("attempt_id") == source_attempt_id
            and item.get("decision") == "APPROVE_INTEGRATION"
            for item in current["review_decisions"]
        ):
            self.record_review_decision(
                task_id,
                attempt_id=source_attempt_id,
                decision="APPROVE_INTEGRATION",
                findings=[note],
                evidence_refs=[{"kind": "verified_patch", "sha256": verified_patch_digest}],
            )
        host_record_integration(
            self.root,
            self.run_id,
            task_id,
            note=note,
            target_ref=target_ref,
            integration_revision=integration_revision,
            source_attempt_id=source_attempt_id,
            verified_patch_digest=verified_patch_digest,
        )
        plan = refresh_plan(self.store.load(self.run_id))
        metadata = normalize_supervisor_metadata(plan.get("supervisor"))
        metadata["status"] = "COMPLETED" if plan["status"] == "INTEGRATED" else "ACTIVE"
        metadata["next_action"] = "stop" if metadata["status"] == "COMPLETED" else "advance"
        return self._step(self._save_supervisor(metadata))


def providers_for_resume(root: Path, run_id: str, timeout_seconds: float) -> dict[str, Any]:
    """Backward-compatible Supervisor boundary for assigned Provider composition."""

    return compose_providers_for_resume(root, run_id, timeout_seconds)


def _providers_for_resume(root: Path, run_id: str, timeout_seconds: float) -> dict[str, Any]:
    """Backward-compatible alias for older in-process callers."""

    return providers_for_resume(root, run_id, timeout_seconds)


def cli_artifact_reference(value: str, *, kind: str = "artifact") -> dict[str, str]:
    """Public Supervisor CLI artifact-reference boundary."""

    return artifact_reference(value, kind=kind)


def _cli_artifact_reference(value: str, *, kind: str = "artifact") -> dict[str, str]:
    """Backward-compatible alias for older CLI callers."""

    return cli_artifact_reference(value, kind=kind)


def latest_rework_decision(plan: Mapping[str, Any], task_id: str, attempt_id: str) -> Mapping[str, Any]:
    """Public compatibility boundary for the shared read-only plan query."""

    return query_latest_rework_decision(plan, task_id, attempt_id)


def _latest_rework_decision(plan: Mapping[str, Any], task_id: str, attempt_id: str) -> Mapping[str, Any]:
    """Backward-compatible alias for older in-process callers."""

    return latest_rework_decision(plan, task_id, attempt_id)


def main(argv: list[str] | None = None) -> int:
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
    args = parser.parse_args(argv)
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
    step = runner.advance(
        providers=providers,
        expected_remaining_seconds=args.expected_remaining_seconds,
        verification_trust_level=args.trust_level,
        operator_approved=args.operator_approved,
        execution_boundary=args.execution_boundary,
    ) if args.command == "resume" else runner.run_until_intervention(
        providers=providers,
        expected_remaining_seconds=args.expected_remaining_seconds,
        verification_trust_level=args.trust_level,
        operator_approved=args.operator_approved,
        dispatch_timeout_seconds=args.dispatch_timeout_seconds,
        max_wait_seconds=args.max_wait_seconds,
        execution_boundary=args.execution_boundary,
    )
    print(json.dumps(step.to_dict(), ensure_ascii=False, indent=2))
    return 0


__all__ = [
    "CodexSupervisedCommanderRun",
    "CodexLessEvaluation",
    "SupervisorStep",
    "cli_artifact_reference",
    "latest_rework_decision",
    "main",
    "providers_for_resume",
]


if __name__ == "__main__":
    raise SystemExit(main())
