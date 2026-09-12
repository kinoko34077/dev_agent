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
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.devfarm import DevFarmError
from scripts.devfarm_commander import (
    CommanderPlanStore,
    _verified_worker_patch,
    _record_result,
    collect_plan,
    dispatch_plan,
    mark_integrated,
    recover_orphaned_dispatches,
    reassign_task,
    refresh_plan,
    verify_plan,
)
from scripts.devfarm_supervisor_protocol import (
    advance_heartbeat,
    normalize_supervisor_metadata,
    normalize_review_decision,
    normalize_review_packet,
    record_wake,
    select_heartbeat_cadence,
)
from scripts.devfarm import validate_patch
from src.dev_agent.handoff import ExternalTextReference, HandoffEnvelope, rework_request


def _git_process(cwd: Path, *arguments: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={cwd.as_posix()}", *arguments],
        cwd=cwd,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
    )


def _git_output(cwd: Path, *arguments: str) -> str:
    result = _git_process(cwd, *arguments)
    if result.returncode != 0:
        raise DevFarmError(result.stderr.strip() or result.stdout.strip() or "Git command failed")
    return result.stdout.strip()


def _git_status(cwd: Path) -> str:
    # .devfarm is the existing ignored/development artifact area.  It must
    # never be staged by this helper, but its untracked attempt files should
    # not make an otherwise clean integration checkout unusable.
    return _git_output(
        cwd,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        ".",
        ":(exclude).devfarm",
    )


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
        }


class CodexSupervisedCommanderRun:
    """Manage one bounded supervisor pass over a durable Commander plan."""

    def __init__(self, root: str | Path, run_id: str) -> None:
        self.root = Path(root).resolve()
        self.run_id = run_id
        self.store = CommanderPlanStore(self.root)

    def plan(self) -> dict[str, Any]:
        return self.store.load(self.run_id)

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
        )

    def _review_packet(self, task: Mapping[str, Any]) -> dict[str, Any]:
        """Build a bounded packet from Host-side artifacts, never raw output."""

        task_id = str(task["task_id"])
        attempt_id = task.get("last_attempt_id")
        if not isinstance(attempt_id, str) or not attempt_id.strip():
            raise DevFarmError(f"review packet requires an attempt id: {task_id}")
        result_ref = task.get("result_ref")
        if not isinstance(result_ref, str) or not result_ref.strip():
            raise DevFarmError(f"review packet requires a result reference: {task_id}")
        result_path = (self.root / result_ref).resolve()
        try:
            result_path.relative_to(self.root)
        except ValueError as exc:
            raise DevFarmError("review result reference escapes repository") from exc
        result: Mapping[str, Any] = {}
        if result_path.is_file():
            try:
                loaded = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise DevFarmError("review result artifact cannot be read") from exc
            if not isinstance(loaded, Mapping):
                raise DevFarmError("review result artifact must be an object")
            result = loaded
        assignment = task.get("assignment", {})
        if not isinstance(assignment, Mapping):
            assignment = {}
        worker_metrics = result.get("worker_metrics", {})
        if not isinstance(worker_metrics, Mapping):
            worker_metrics = {}
        verification_id = result.get("verification_id")
        attempt_root = result_path.parent
        verification_ref = None
        if isinstance(verification_id, str) and verification_id.strip():
            verification_ref = (attempt_root / "verification" / f"{verification_id}.json").relative_to(self.root).as_posix()
        patch_path = attempt_root / "patch.diff"
        patch_ref = patch_path.relative_to(self.root).as_posix()
        patch_sha256 = task.get("verified_patch_digest")
        if patch_path.is_file():
            patch_sha256 = hashlib.sha256(patch_path.read_bytes()).hexdigest()
        if task.get("verified_patch_digest") is not None and task["verified_patch_digest"] != patch_sha256:
            raise DevFarmError("review packet patch digest does not match the Host artifact")
        manifest_ref = task.get("manifest_path")
        manifest: Mapping[str, Any] = {}
        if isinstance(manifest_ref, str):
            manifest_path = (self.root / manifest_ref).resolve()
            try:
                manifest_path.relative_to(self.root)
                if manifest_path.is_file():
                    loaded_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if isinstance(loaded_manifest, Mapping):
                        manifest = loaded_manifest
            except (ValueError, OSError, json.JSONDecodeError):
                manifest = {}
        summary = {
            key: worker_metrics[key]
            for key in (
                "host_verified",
                "host_verified_test_count",
                "host_tests_passed",
                "independent_verification",
                "result_accepted",
                "verification_trust_level",
                "operator_approved",
            )
            if key in worker_metrics and isinstance(worker_metrics[key], (str, int, bool, float))
        }
        return normalize_review_packet(
            {
                "task_id": task_id,
                "attempt_id": attempt_id,
                "status": task.get("status", result.get("status", "unknown")),
                "provider": assignment.get("provider_id"),
                "model": assignment.get("model_id"),
                "changed_files": result.get("changed_files", []),
                "patch_sha256": patch_sha256,
                "result_ref": result_ref,
                "verification_ref": verification_ref,
                "verification_summary": summary,
                "known_issues": result.get("known_issues", []),
                "acceptance": manifest.get("acceptance", []),
                "artifact_refs": [
                    {"kind": "result", "path": result_ref},
                    {"kind": "patch", "path": patch_ref},
                    *([{"kind": "verification", "path": verification_ref}] if verification_ref else []),
                ],
                "created_at": task.get("updated_at"),
            }
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
            _record_result(
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
            _record_result(
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
                dispatch_plan(
                    self.root,
                    self.run_id,
                    providers=providers,
                    orchestrator=orchestrator,
                    dispatch_timeout_seconds=dispatch_timeout_seconds,
                )
            except (DevFarmError, TypeError, ValueError) as exc:
                metadata = normalize_supervisor_metadata(self.store.load(self.run_id).get("supervisor"))
                metadata["status"] = "HUMAN_DECISION_REQUIRED"
                metadata["next_action"] = "resolve_worker_admission"
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
                current_metadata["status"] = "HUMAN_DECISION_REQUIRED"
                current_metadata["next_action"] = "supervisor_overall_deadline"
                current_metadata = record_wake(current_metadata, kind="SUPERVISOR_OVERALL_DEADLINE")
                return self._step(self._save_supervisor(current_metadata))
            elapsed = max(0.0, float(monotonic_fn()) - started)
            remaining = float(max_wait_seconds) - elapsed
            if remaining <= 0:
                metadata = normalize_supervisor_metadata(self.store.load(self.run_id).get("supervisor"))
                metadata["status"] = "HUMAN_DECISION_REQUIRED"
                metadata["next_action"] = "supervisor_wait_deadline"
                metadata = record_wake(metadata, kind="SUPERVISOR_WAIT_DEADLINE")
                return self._step(self._save_supervisor(metadata))

            step = self.advance(
                providers=providers,
                orchestrator=orchestrator,
                expected_remaining_seconds=expected_remaining_seconds,
                verification_trust_level=verification_trust_level,
                operator_approved=operator_approved,
                dispatch_timeout_seconds=dispatch_timeout_seconds,
            )
            if step.status in {
                "REVIEWING",
                "HUMAN_DECISION_REQUIRED",
                "COMPLETED",
                "BLOCKED",
            }:
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
        """Apply and commit one already-approved, Host-verified patch.

        The reviewer only supplies the durable decision.  All Git mutation is
        deterministic Host work after verification proof is re-read.  This
        helper never pushes, merges, edits protected files, or changes Gate
        state.
        """

        target = Path(target_checkout).resolve()
        if not target.is_dir():
            raise DevFarmError("integration target checkout does not exist")
        if not isinstance(target_ref, str) or not target_ref.strip():
            raise DevFarmError("integration target_ref must be non-empty")
        if not isinstance(commit_message, str) or not commit_message.strip() or len(commit_message.strip()) > 200:
            raise DevFarmError("integration commit_message must be 1-200 characters")
        plan = self.store.load(self.run_id)
        task = next((item for item in plan["tasks"] if item["task_id"] == task_id), None)
        if task is None:
            raise DevFarmError(f"Commander task does not exist: {task_id}")
        if task.get("status") != "HOST_VERIFIED":
            raise DevFarmError("integration requires a HOST_VERIFIED worker task")
        decision = next((item for item in plan["review_decisions"] if item["decision_id"] == decision_id), None)
        if decision is None:
            raise DevFarmError("durable approval decision is missing")
        if decision.get("task_id") != task_id or decision.get("attempt_id") != task.get("last_attempt_id"):
            raise DevFarmError("approval decision does not match the verified attempt")
        if decision.get("decision") != "APPROVE_INTEGRATION":
            raise DevFarmError("integration requires APPROVE_INTEGRATION decision")
        patch, manifest, attempt_id = _verified_worker_patch(self.root, task)
        changed_files = validate_patch(patch, manifest=manifest)
        patch_digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        if task.get("verified_patch_digest") is not None and task["verified_patch_digest"] != patch_digest:
            raise DevFarmError("verified patch digest does not match the task record")
        if _git_status(target):
            raise DevFarmError("integration target checkout must be clean")
        target_revision = _git_output(target, "rev-parse", target_ref)
        try:
            _git_output(target, "merge-base", "--is-ancestor", manifest["base_revision"], target_revision)
        except DevFarmError as exc:
            raise DevFarmError("integration target does not contain the worker base revision") from exc
        checked = _git_process(target, "apply", "--check", "--whitespace=error", "-", input_text=patch)
        if checked.returncode != 0:
            raise DevFarmError(checked.stderr.strip() or checked.stdout.strip() or "verified patch does not apply")
        applied = _git_process(target, "apply", "--whitespace=error", "-", input_text=patch)
        if applied.returncode != 0:
            raise DevFarmError(applied.stderr.strip() or applied.stdout.strip() or "verified patch application failed")
        _git_output(target, "add", "--", *changed_files)
        _git_output(target, "diff", "--cached", "--check")
        _git_output(target, "commit", "-m", commit_message.strip())
        integration_revision = _git_output(target, "rev-parse", "HEAD")
        mark_integrated(
            self.root,
            self.run_id,
            task_id,
            note=f"approved by review decision {decision_id}",
            target_ref=target_ref,
            integration_revision=integration_revision,
            source_attempt_id=attempt_id,
            verified_patch_digest=patch_digest,
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
        mark_integrated(
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


def _providers_for_resume(root: Path, run_id: str, timeout_seconds: float) -> dict[str, Any]:
    """Construct only the already assigned providers needed for one pass."""

    from scripts.devfarm_worker import _provider

    runner = CodexSupervisedCommanderRun(root, run_id)
    providers: dict[str, Any] = {}
    for task in runner.plan()["tasks"]:
        if task["owner"] != "worker" or task["status"] not in {"PLANNED", "READY"}:
            continue
        assignment = task["assignment"]
        providers[task["task_id"]] = _provider(
            assignment["provider_id"],
            assignment["model_id"],
            timeout_seconds,
        )
    return providers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="bounded Codex supervisor view over a Commander plan")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("status", "resume", "run"):
        item = sub.add_parser(command)
        item.add_argument("run_id")
        item.add_argument("--root", type=Path, default=Path.cwd())
    resume = sub.choices["resume"]
    resume.add_argument("--expected-remaining-seconds", type=float)
    resume.add_argument("--timeout-seconds", type=float, default=30.0)
    resume.add_argument("--trust-level", choices=("STATIC_ONLY", "TRUSTED_HOST_EXEC", "OS_SANDBOXED"), default="STATIC_ONLY")
    resume.add_argument("--operator-approved", action="store_true")
    run = sub.choices["run"]
    run.add_argument("--expected-remaining-seconds", type=float)
    run.add_argument("--timeout-seconds", type=float, default=30.0)
    run.add_argument("--dispatch-timeout-seconds", type=float, default=300.0)
    run.add_argument("--max-wait-seconds", type=float, default=900.0)
    run.add_argument("--trust-level", choices=("STATIC_ONLY", "TRUSTED_HOST_EXEC", "OS_SANDBOXED"), default="STATIC_ONLY")
    run.add_argument("--operator-approved", action="store_true")
    args = parser.parse_args(argv)
    runner = CodexSupervisedCommanderRun(args.root, args.run_id)
    if args.command == "status":
        print(json.dumps(runner.status().to_dict(), ensure_ascii=False, indent=2))
        return 0
    providers = _providers_for_resume(args.root, args.run_id, args.timeout_seconds)
    step = runner.advance(
        providers=providers,
        expected_remaining_seconds=args.expected_remaining_seconds,
        verification_trust_level=args.trust_level,
        operator_approved=args.operator_approved,
    ) if args.command == "resume" else runner.run_until_intervention(
        providers=providers,
        expected_remaining_seconds=args.expected_remaining_seconds,
        verification_trust_level=args.trust_level,
        operator_approved=args.operator_approved,
        dispatch_timeout_seconds=args.dispatch_timeout_seconds,
        max_wait_seconds=args.max_wait_seconds,
    )
    print(json.dumps(step.to_dict(), ensure_ascii=False, indent=2))
    return 0


__all__ = ["CodexSupervisedCommanderRun", "SupervisorStep", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
