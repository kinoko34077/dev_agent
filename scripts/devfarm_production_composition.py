"""Thin Phase 8 production-composition boundaries.

This module is intentionally not a second scheduler.  It owns the explicit
submit/observe boundary between callers and the existing Operation/DevFarm
authorities, plus the identity seam between their durable projections.  The
two projections deliberately use different UUID namespaces, so integration
evidence must be joined by the validated planner identity
(``proposal_id + child_key``), never by coincidental task IDs.

The facade delegates to injected production boundaries.  It does not sequence
Planner, Worker, Host Verification, Review, Integration, or dependency release
itself; those remain owned by the existing runtime and Supervisor paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from src.dev_agent.domain.protocol import Task
from src.dev_agent.intelligence.planner import RootPlanningProposal


class ProductionCompositionError(ValueError):
    """A production projection cannot be joined without ambiguity."""


_MAX_PROJECTION_DEPTH = 3
_MAX_PROJECTION_ITEMS = 64
_MAX_PROJECTION_TEXT = 2048
_FORBIDDEN_PROJECTION_TERMS = (
    "api_key",
    "apikey",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
    "raw_response",
    "raw_output",
)


def _bounded_projection(value: Any, *, depth: int = 0) -> Any:
    """Copy a small, secret-free result projection without invoking policy."""

    if depth > _MAX_PROJECTION_DEPTH:
        raise ProductionCompositionError("boundary result exceeds bounded projection depth")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > _MAX_PROJECTION_TEXT:
            raise ProductionCompositionError("boundary result exceeds bounded text")
        return value
    if isinstance(value, Mapping):
        if len(value) > _MAX_PROJECTION_ITEMS:
            raise ProductionCompositionError("boundary result exceeds bounded mapping")
        projected: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ProductionCompositionError("boundary result has an invalid mapping key")
            normalized_key = key.strip()
            lowered_key = normalized_key.casefold()
            if any(term in lowered_key for term in _FORBIDDEN_PROJECTION_TERMS):
                raise ProductionCompositionError("boundary result is not bounded")
            projected[normalized_key] = _bounded_projection(item, depth=depth + 1)
        return projected
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_PROJECTION_ITEMS:
            raise ProductionCompositionError("boundary result exceeds bounded sequence")
        return [_bounded_projection(item, depth=depth + 1) for item in value]
    raise ProductionCompositionError("boundary result contains an unsupported value")


class Phase8ProductionComposition:
    """Expose only the production submit/observe boundary.

    The injected callables are composed from existing durable authorities by
    the embedding runtime.  Keeping them injected makes it impossible for
    this facade to create a second queue, retry loop, or integration path.
    """

    def __init__(self, *, submit_boundary: Any, observe_boundary: Any) -> None:
        if not callable(submit_boundary) or not callable(observe_boundary):
            raise TypeError("submit and observe boundary callables are required")
        self._submit_boundary = submit_boundary
        self._observe_boundary = observe_boundary

    def submit(self, objective: str, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(objective, str) or not objective.strip() or len(objective.strip()) > 4000:
            raise ValueError("objective must be bounded non-empty text")
        result = self._submit_boundary(objective.strip(), **kwargs)
        projected = _bounded_projection(result)
        if not isinstance(projected, dict):
            raise ProductionCompositionError("submit boundary must return a mapping")
        return projected

    def observe(self, run_id: str, **kwargs: Any) -> dict[str, Any]:
        if not isinstance(run_id, str) or not run_id.strip() or len(run_id.strip()) > 256:
            raise ValueError("run_id must be bounded non-empty text")
        result = self._observe_boundary(run_id.strip(), **kwargs)
        projected = _bounded_projection(result)
        if not isinstance(projected, dict):
            raise ProductionCompositionError("observe boundary must return a mapping")
        return projected


class Phase8ProductionExecutor:
    """Advance one handed-off Phase 8 root through existing authorities.

    This is a bounded composition step, not a scheduler.  The caller owns
    when to invoke it.  Worker dispatch, Host Verification, review decision
    persistence, deterministic Git integration, and Operation dependency
    release remain owned by their existing boundaries.

    The executor exists because the thin submit/observe facade cannot safely
    hide the cross-plane identity and evidence return.  It validates the
    complete Commander projection before invoking ``advance`` so a stale or
    partial handoff cannot cause a second execution owner to mutate state.
    """

    _DECISIONS = frozenset({
        "APPROVE_INTEGRATION",
        "REWORK",
        "REJECT",
        "ESCALATE",
    })

    def __init__(
        self,
        *,
        operation: Any,
        commander: Any,
        proposal_id: str,
        devfarm_run_id: str,
        bindings: Mapping[str, str],
        providers: Mapping[str, Any],
        orchestrator: Any,
        review_proposal: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        final_review_decision: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
        target_checkout: str | Path,
        target_ref: str = "HEAD",
        verification_trust_level: str = "TRUSTED_HOST_EXEC",
        operator_approved: bool = True,
        dispatch_timeout_seconds: int | float = 300.0,
    ) -> None:
        required_operation = "record_devfarm_integration_evidence"
        required_commander = (
            "advance",
            "plan",
            "review_packet",
            "record_review_decision",
            "integrate_approved_worker",
        )
        if not callable(getattr(operation, required_operation, None)):
            raise TypeError("operation integration evidence boundary is required")
        if any(not callable(getattr(commander, name, None)) for name in required_commander):
            raise TypeError("Commander production boundaries are incomplete")
        if not isinstance(proposal_id, str) or not proposal_id.strip() or len(proposal_id.strip()) > 256:
            raise ValueError("proposal_id must be bounded non-empty text")
        if not isinstance(devfarm_run_id, str) or not devfarm_run_id.strip() or len(devfarm_run_id.strip()) > 256:
            raise ValueError("devfarm_run_id must be bounded non-empty text")
        if not isinstance(bindings, Mapping) or not bindings:
            raise ValueError("bindings must be a non-empty mapping")
        normalized_bindings: dict[str, str] = {}
        for child_key, task_id in bindings.items():
            if not isinstance(child_key, str) or not child_key.strip():
                raise ValueError("binding child keys must be non-empty text")
            if not isinstance(task_id, str) or not task_id.strip() or len(task_id.strip()) > 256:
                raise ValueError("binding task identities must be bounded non-empty text")
            normalized_key = child_key.strip()
            normalized_task_id = task_id.strip()
            if normalized_key in normalized_bindings or normalized_task_id in normalized_bindings.values():
                raise ValueError("bindings must contain unique child/task identities")
            normalized_bindings[normalized_key] = normalized_task_id
        if not isinstance(providers, Mapping) or not providers:
            raise ValueError("providers must be a non-empty mapping")
        if not callable(review_proposal) or not callable(final_review_decision):
            raise TypeError("review proposal and final decision boundaries are required")
        if not isinstance(target_checkout, (str, Path)):
            raise TypeError("target_checkout must be a path")
        if not isinstance(target_ref, str) or not target_ref.strip() or len(target_ref.strip()) > 256:
            raise ValueError("target_ref must be bounded non-empty text")
        if verification_trust_level not in {"STATIC_ONLY", "TRUSTED_HOST_EXEC", "OS_SANDBOXED"}:
            raise ValueError("unsupported verification trust level")
        if not isinstance(operator_approved, bool):
            raise TypeError("operator_approved must be a boolean")
        if (
            isinstance(dispatch_timeout_seconds, bool)
            or not isinstance(dispatch_timeout_seconds, (int, float))
            or dispatch_timeout_seconds <= 0
            or dispatch_timeout_seconds > 900
        ):
            raise ValueError("dispatch_timeout_seconds must be between 0 and 900")
        self.operation = operation
        self.commander = commander
        self.proposal_id = proposal_id.strip()
        self.devfarm_run_id = devfarm_run_id.strip()
        self.bindings = normalized_bindings
        self.providers = dict(providers)
        self.orchestrator = orchestrator
        self.review_proposal = review_proposal
        self.final_review_decision = final_review_decision
        self.target_checkout = str(Path(target_checkout))
        self.target_ref = target_ref.strip()
        self.verification_trust_level = verification_trust_level
        self.operator_approved = operator_approved
        self.dispatch_timeout_seconds = float(dispatch_timeout_seconds)

    @staticmethod
    def _worker_tasks(plan: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        tasks = plan.get("tasks")
        if isinstance(tasks, (str, bytes)) or not isinstance(tasks, Sequence):
            raise ProductionCompositionError("Commander plan tasks are unavailable")
        workers: list[Mapping[str, Any]] = []
        for task in tasks:
            if not isinstance(task, Mapping):
                raise ProductionCompositionError("Commander plan contains an invalid task")
            if task.get("owner") == "worker":
                workers.append(task)
        if not workers:
            raise ProductionCompositionError("Commander plan has no Worker children")
        return tuple(workers)

    def _validate_worker_identity(self, plan: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
        workers = self._worker_tasks(plan)
        child_keys: list[str] = []
        task_ids: list[str] = []
        for task in workers:
            if task.get("planning_proposal_id") != self.proposal_id:
                raise ProductionCompositionError("Commander Worker has mismatched planner proposal identity")
            child_key = task.get("planner_child_key")
            task_id = task.get("task_id")
            if not isinstance(child_key, str) or not child_key.strip():
                raise ProductionCompositionError("Commander Worker has no planner child identity")
            if not isinstance(task_id, str) or not task_id.strip():
                raise ProductionCompositionError("Commander Worker has no durable task identity")
            child_keys.append(child_key.strip())
            task_ids.append(task_id.strip())
        if len(set(child_keys)) != len(child_keys) or len(set(task_ids)) != len(task_ids):
            raise ProductionCompositionError("Commander Worker identities are ambiguous")
        if set(child_keys) != set(self.bindings):
            raise ValueError("bindings must cover the exact worker identity set")
        if set(task_ids) != set(self.providers):
            raise ValueError("providers must cover the exact worker task set")
        commander_by_key = {
            child_key: task_id
            for child_key, task_id in zip(child_keys, task_ids, strict=True)
        }
        if any(self.bindings[key] != commander_by_key[key] for key in self.bindings):
            raise ProductionCompositionError("DevFarm handoff identity does not match Commander task identity")
        return tuple(
            next(task for task in workers if task.get("planner_child_key") == child_key)
            for child_key in self.bindings
        )

    @classmethod
    def _normalize_review_decision(cls, value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ProductionCompositionError("review decision boundary must return a mapping")
        decision = value.get("decision")
        if not isinstance(decision, str) or decision.strip() not in cls._DECISIONS:
            raise ProductionCompositionError("review decision is invalid")
        findings = value.get("findings", [])
        if isinstance(findings, (str, bytes)) or not isinstance(findings, list) or len(findings) > 32:
            raise ProductionCompositionError("review findings are invalid")
        normalized_findings: list[str] = []
        for finding in findings:
            if not isinstance(finding, str) or not finding.strip() or len(finding.strip()) > 2000:
                raise ProductionCompositionError("review findings are invalid")
            normalized_findings.append(finding.strip())
        evidence_refs = value.get("evidence_refs", [])
        if isinstance(evidence_refs, (str, bytes)) or not isinstance(evidence_refs, list) or len(evidence_refs) > 32:
            raise ProductionCompositionError("review evidence references are invalid")
        normalized_refs: list[Mapping[str, Any]] = []
        for reference in evidence_refs:
            if not isinstance(reference, Mapping):
                raise ProductionCompositionError("review evidence references are invalid")
            projected = _bounded_projection(reference)
            if not isinstance(projected, dict):
                raise ProductionCompositionError("review evidence references are invalid")
            normalized_refs.append(projected)
        correction = value.get("required_correction")
        if correction is not None and (
            not isinstance(correction, str) or not correction.strip() or len(correction.strip()) > 2000
        ):
            raise ProductionCompositionError("review correction is invalid")
        role = value.get("reviewer_role", "reviewer")
        if not isinstance(role, str) or not role.strip() or len(role.strip()) > 128:
            raise ProductionCompositionError("reviewer role is invalid")
        normalized: dict[str, Any] = {
            "decision": decision.strip(),
            "findings": normalized_findings,
            "evidence_refs": normalized_refs,
            "reviewer_role": role.strip(),
        }
        if correction is not None:
            normalized["required_correction"] = correction.strip()
        return normalized

    @staticmethod
    def _decision_for_task(plan: Mapping[str, Any], task_id: str, attempt_id: str | None) -> Mapping[str, Any] | None:
        decisions = plan.get("review_decisions", [])
        if isinstance(decisions, (str, bytes)) or not isinstance(decisions, Sequence):
            raise ProductionCompositionError("Commander review decisions are unavailable")
        for decision in decisions:
            if not isinstance(decision, Mapping):
                continue
            if decision.get("task_id") == task_id and decision.get("attempt_id") == attempt_id:
                return decision
        return None

    @staticmethod
    def _task_projection(task: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in ("task_id", "planner_child_key", "status", "last_attempt_id", "integration_revision"):
            value = task.get(key)
            if isinstance(value, str) and value.strip():
                result[key] = value.strip()
        return result

    @staticmethod
    def _operation_change_projection(value: Any) -> dict[str, Any]:
        if isinstance(value, Task):
            return {
                "task_id": value.task_id,
                "state": value.status.value,
                "planner_child_key": value.metadata.get("planner_child_key"),
            }
        if isinstance(value, Mapping):
            task_id = value.get("task_id")
            status = value.get("status", value.get("state"))
            if not isinstance(task_id, str) or not task_id.strip() or not isinstance(status, str):
                raise ProductionCompositionError("Operation evidence result is not bounded")
            return {
                "task_id": task_id.strip(),
                "state": status.strip(),
                "planner_child_key": value.get("planner_child_key"),
            }
        raise ProductionCompositionError("Operation evidence result is not bounded")

    def observe(self) -> dict[str, Any]:
        """Return the current bounded Commander projection without mutation."""

        plan = self.commander.plan()
        workers = self._validate_worker_identity(plan)
        return {
            "run_id": self.devfarm_run_id,
            "proposal_id": self.proposal_id,
            "commander_status": plan.get("status"),
            "workers": [self._task_projection(task) for task in workers],
            "continuation_ready": False,
        }

    def advance(self) -> dict[str, Any]:
        """Run one existing DevFarm pass and return bounded evidence."""

        initial_plan = self.commander.plan()
        self._validate_worker_identity(initial_plan)
        step = self.commander.advance(
            providers=self.providers,
            orchestrator=self.orchestrator,
            verification_trust_level=self.verification_trust_level,
            operator_approved=self.operator_approved,
            dispatch_timeout_seconds=self.dispatch_timeout_seconds,
        )
        plan = self.commander.plan()
        workers = self._validate_worker_identity(plan)
        integrated: list[str] = []
        reviewed: list[str] = []
        operation_changes: list[dict[str, Any]] = []
        for task in workers:
            task_id = str(task["task_id"])
            child_key = str(task["planner_child_key"])
            attempt_id = task.get("last_attempt_id")
            if not isinstance(attempt_id, str) or not attempt_id.strip():
                continue
            decision = self._decision_for_task(plan, task_id, attempt_id)
            if decision is None and task.get("status") == "HOST_VERIFIED":
                packet = self.commander.review_packet(task_id, attempt_id=attempt_id)
                proposal = self._normalize_review_decision(self.review_proposal(packet))
                final = self._normalize_review_decision(self.final_review_decision(packet, proposal))
                self.commander.record_review_decision(
                    task_id,
                    attempt_id=attempt_id,
                    decision=final["decision"],
                    findings=final["findings"],
                    evidence_refs=final["evidence_refs"],
                    required_correction=final.get("required_correction"),
                    reviewer_role=final["reviewer_role"],
                )
                reviewed.append(child_key)
                plan = self.commander.plan()
                decision = self._decision_for_task(plan, task_id, attempt_id)
            if decision is None or decision.get("decision") != "APPROVE_INTEGRATION":
                continue
            if task.get("status") == "INTEGRATED":
                continue
            decision_id = decision.get("decision_id")
            if not isinstance(decision_id, str) or not decision_id.strip():
                raise ProductionCompositionError("approved review decision has no durable identity")
            self.commander.integrate_approved_worker(
                task_id,
                decision_id=decision_id,
                commit_message=f"integrate Phase 8 {child_key}",
                target_checkout=self.target_checkout,
                target_ref=self.target_ref,
            )
            integrated_task = next(
                item for item in self.commander.plan()["tasks"] if item.get("task_id") == task_id
            )
            integration_revision = integrated_task.get("integration_revision")
            source_attempt_id = integrated_task.get("source_attempt_id") or integrated_task.get("last_attempt_id")
            digest = integrated_task.get("verified_patch_digest")
            if not all(isinstance(value, str) and value.strip() for value in (integration_revision, source_attempt_id, digest)):
                raise ProductionCompositionError("integrated Commander task lacks bounded evidence")
            changes = self.operation.record_devfarm_integration_evidence(
                proposal_id=self.proposal_id,
                child_key=child_key,
                devfarm_run_id=self.devfarm_run_id,
                devfarm_task_id=self.bindings[child_key],
                status="INTEGRATED",
                host_verification="PASS",
                review_decision="APPROVE_INTEGRATION",
                integration_revision=integration_revision,
                source_attempt_id=source_attempt_id,
                verified_patch_digest=digest,
            )
            if not isinstance(changes, Sequence) or isinstance(changes, (str, bytes)):
                raise ProductionCompositionError("Operation integration boundary returned an invalid result")
            operation_changes.extend(self._operation_change_projection(item) for item in changes)
            integrated.append(child_key)
            plan = self.commander.plan()

        continuation_ready = any(
            item["state"] in {"queued", "waiting_dependency"}
            and item.get("planner_child_key") not in set(self.bindings)
            for item in operation_changes
        )
        commander_status = getattr(step, "status", plan.get("status"))
        return {
            "run_id": self.devfarm_run_id,
            "proposal_id": self.proposal_id,
            "commander_status": commander_status,
            "reviewed": reviewed,
            "integrated": integrated,
            "operation_changes": operation_changes,
            "continuation_ready": continuation_ready,
        }


@dataclass(frozen=True)
class _DevelopmentTaskLink:
    child_key: str
    operation_task_id: str
    commander_task_id: str


def _development_task_links(
    proposal: RootPlanningProposal,
    operation_children: Sequence[Task],
    commander_tasks: Sequence[Mapping[str, Any]],
) -> tuple[_DevelopmentTaskLink, ...]:
    """Join Operation and Commander tasks by the planner's stable child key.

    Operation children and Commander candidates persist
    ``planning_proposal_id`` and ``planner_child_key``.  Commander generates a
    different deterministic task UUID, so this function validates the
    semantic identity on both projections before returning an explicit
    mapping used by the future composition owner. Planner child order is used
    only for deterministic presentation after the identity join.
    """

    if not isinstance(proposal, RootPlanningProposal):
        raise TypeError("proposal must be a RootPlanningProposal")
    expected_keys = tuple(child.child_key for child in proposal.children)

    operation_by_key: dict[str, Task] = {}
    for task in operation_children:
        if not isinstance(task, Task):
            raise TypeError("operation_children must contain Task values")
        if task.metadata.get("planning_proposal_id") != proposal.proposal_id:
            raise ProductionCompositionError("operation child belongs to another planning proposal")
        child_key = task.metadata.get("planner_child_key")
        if not isinstance(child_key, str) or child_key not in expected_keys:
            raise ProductionCompositionError("operation child has no validated planner child key")
        if child_key in operation_by_key:
            raise ProductionCompositionError(f"duplicate operation child key: {child_key}")
        operation_by_key[child_key] = task

    if len(commander_tasks) != len(expected_keys):
        raise ProductionCompositionError("Commander projection is missing planner children")
    commander_by_key: dict[str, Mapping[str, Any]] = {}
    for task in commander_tasks:
        if not isinstance(task, Mapping):
            raise TypeError("commander_tasks must contain mappings")
        commander_proposal_id = task.get("planning_proposal_id")
        if commander_proposal_id is None:
            raise ProductionCompositionError("Commander task has no validated planner identity")
        if commander_proposal_id != proposal.proposal_id:
            raise ProductionCompositionError("Commander task belongs to another planning proposal")
        child_key = task.get("planner_child_key")
        if not isinstance(child_key, str) or not child_key.strip():
            raise ProductionCompositionError("Commander task has no validated planner child key")
        if child_key not in expected_keys:
            raise ProductionCompositionError("Commander task has an unknown planner child key")
        if child_key in commander_by_key:
            raise ProductionCompositionError(f"duplicate Commander child key: {child_key}")
        task_id = task.get("task_id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ProductionCompositionError("Commander task has no durable task_id")
        commander_by_key[child_key] = task

    if set(operation_by_key) != set(expected_keys):
        raise ProductionCompositionError("Operation projection is missing planner children")
    if set(commander_by_key) != set(expected_keys):
        raise ProductionCompositionError("Commander projection is missing planner children")

    return tuple(
        _DevelopmentTaskLink(
            child_key=child_key,
            operation_task_id=operation_by_key[child_key].task_id,
            commander_task_id=str(commander_by_key[child_key]["task_id"]),
        )
        for child_key in expected_keys
    )


__all__ = ["Phase8ProductionComposition", "Phase8ProductionExecutor", "ProductionCompositionError"]
