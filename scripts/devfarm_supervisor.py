"""Bounded Codex-supervised composition over the existing Commander primitives.

The supervisor is deliberately a one-pass facade.  It never starts a
background scheduler, auto-integrates, or treats Worker output as authority.
The caller may invoke ``advance`` again after a durable wake/heartbeat.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.devfarm import DevFarmError
from scripts.devfarm_commander import (
    CommanderPlanStore,
    collect_plan,
    dispatch_plan,
    mark_integrated,
    reassign_task,
    refresh_plan,
    verify_plan,
)
from scripts.devfarm_supervisor_protocol import (
    advance_heartbeat,
    normalize_supervisor_metadata,
    record_wake,
    select_heartbeat_cadence,
)
from src.dev_agent.handoff import ExternalTextReference, HandoffEnvelope, rework_request


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
            metrics=metadata["metrics"],
        )

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
    ) -> SupervisorStep:
        """Run exactly one bounded refresh/dispatch/collect/verify pass."""

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
                dispatch_plan(self.root, self.run_id, providers=providers, orchestrator=orchestrator)
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
        for task in plan["tasks"]:
            if task["status"] == "HOST_VERIFIED":
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
        metadata["metrics"]["codex_review_count"] += newly_verified
        metadata["metrics"]["worker_success_count"] += newly_verified
        metadata["metrics"]["worker_dispatch_count"] += len(ready_worker_ids)

        if all(task["status"] == "INTEGRATED" for task in plan["tasks"]):
            metadata["status"] = "COMPLETED"
            metadata["next_action"] = "stop"
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

    def reassign(self, task_id: str, *, provider_id: str, model_id: str, provider_binding_id: str | None = None) -> SupervisorStep:
        reassign_task(
            self.root,
            self.run_id,
            task_id,
            provider_id=provider_id,
            model_id=model_id,
            provider_binding_id=provider_binding_id,
        )
        metadata = normalize_supervisor_metadata(self.store.load(self.run_id).get("supervisor"))
        metadata["status"] = "ACTIVE"
        metadata["next_action"] = "advance"
        metadata["metrics"]["worker_retry_count"] += 1
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
    for command in ("status", "resume"):
        item = sub.add_parser(command)
        item.add_argument("run_id")
        item.add_argument("--root", type=Path, default=Path.cwd())
    resume = sub.choices["resume"]
    resume.add_argument("--expected-remaining-seconds", type=float)
    resume.add_argument("--timeout-seconds", type=float, default=30.0)
    resume.add_argument("--trust-level", choices=("STATIC_ONLY", "TRUSTED_HOST_EXEC", "OS_SANDBOXED"), default="STATIC_ONLY")
    resume.add_argument("--operator-approved", action="store_true")
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
    )
    print(json.dumps(step.to_dict(), ensure_ascii=False, indent=2))
    return 0


__all__ = ["CodexSupervisedCommanderRun", "SupervisorStep", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
