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
from uuid import uuid4

from scripts.devfarm import write_manifest
from scripts.devfarm_commander import create_plan
from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_manifests import load_worker_manifest
from scripts.devfarm_planning_bridge import DevelopmentPlanningBridge
from scripts.devfarm_refinement import build_concrete_failure_spec
from scripts.devfarm_repository import resolved_revision
from scripts.devfarm_supervisor import CodexSupervisedCommanderRun
from src.dev_agent.domain.protocol import Task, TaskType
from src.dev_agent.intelligence.convergence import RepairDirective
from src.dev_agent.intelligence.planner import PlannerDependencyType, RootPlanningProposal


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


class Phase8ProductionSubmission:
    """Compose one fresh Phase 8 root through existing production boundaries.

    This object is a one-shot submission boundary, not a scheduler.  It
    creates the durable Operation root, accepts one already-proposed Planner
    result, builds the existing Commander plan, records the single-owner
    handoff, and invokes exactly one :class:`Phase8ProductionExecutor` pass.
    External model responses remain injected through ``planner`` and
    ``providers``; all task, queue, verification, review, integration, and
    dependency state remains owned by the existing implementations.
    """

    def __init__(
        self,
        *,
        operation_config: Any,
        repository: str | Path,
        planner: Callable[[Task], RootPlanningProposal],
        task_specs: Callable[[Task, RootPlanningProposal], Mapping[str, Mapping[str, Any]]],
        providers: Callable[[Mapping[str, str]], Mapping[str, Any]],
        fallback_providers: Callable[[Mapping[str, str]], Mapping[str, Any]] | None = None,
        orchestrator: Any,
        review_proposal: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        final_review_decision: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
        target_checkout: str | Path,
        target_ref: str = "HEAD",
        verification_trust_level: str = "TRUSTED_HOST_EXEC",
        operator_approved: bool = True,
        dispatch_timeout_seconds: int | float = 300.0,
        roadmap_reference: Mapping[str, Any] | None = None,
        local_trial: bool = False,
    ) -> None:
        if not callable(planner) or not callable(task_specs) or not callable(providers):
            raise TypeError("Planner, task-spec, and provider boundaries are required")
        if not callable(review_proposal) or not callable(final_review_decision):
            raise TypeError("review boundaries are required")
        if not isinstance(local_trial, bool):
            raise TypeError("local_trial must be a boolean")
        self.operation_config = operation_config
        self.repository = Path(repository).resolve()
        self.planner = planner
        self.task_specs = task_specs
        self.providers = providers
        self.fallback_providers = fallback_providers
        self.orchestrator = orchestrator
        self.review_proposal = review_proposal
        self.final_review_decision = final_review_decision
        self.target_checkout = str(Path(target_checkout).resolve())
        self.target_ref = target_ref
        self.verification_trust_level = verification_trust_level
        self.operator_approved = operator_approved
        self.dispatch_timeout_seconds = dispatch_timeout_seconds
        self.roadmap_reference = dict(roadmap_reference or {})
        self.local_trial = local_trial

    @staticmethod
    def _worker_bindings(plan: Mapping[str, Any]) -> dict[str, str]:
        tasks = plan.get("tasks")
        if isinstance(tasks, (str, bytes)) or not isinstance(tasks, Sequence):
            raise ProductionCompositionError("Commander plan tasks are unavailable")
        bindings: dict[str, str] = {}
        for task in tasks:
            if not isinstance(task, Mapping) or task.get("owner") != "worker":
                continue
            child_key = task.get("planner_child_key")
            task_id = task.get("task_id")
            if not isinstance(child_key, str) or not child_key.strip():
                raise ProductionCompositionError("Commander Worker has no planner child identity")
            if not isinstance(task_id, str) or not task_id.strip():
                raise ProductionCompositionError("Commander Worker has no durable task identity")
            if child_key in bindings or task_id in bindings.values():
                raise ProductionCompositionError("Commander Worker identities are ambiguous")
            bindings[child_key.strip()] = task_id.strip()
        if not bindings:
            raise ProductionCompositionError("Commander plan has no Worker children")
        return bindings

    @staticmethod
    def validate_phase8_root_shape(proposal: RootPlanningProposal) -> None:
        """Reject a non-composable Phase 8 proposal before durable handoff.

        The general Planner validator intentionally accepts arbitrary finite
        DAGs.  This production boundary has a narrower acceptance contract:
        two independent Worker children and one Codex-owned deterministic
        continuation released only after both integrations.  Checking that
        contract before ``apply_planning_proposal`` keeps a malformed model
        proposal out of the durable handoff path and gives a caller a bounded,
        actionable failure for a fresh correction attempt.
        """

        if not isinstance(proposal, RootPlanningProposal):
            raise ProductionCompositionError("Phase 8 Planner proposal is not a RootPlanningProposal")
        if len(proposal.children) != 3:
            raise ProductionCompositionError(
                "Phase 8 production shape requires exactly two Worker children and one continuation"
            )
        workers = tuple(child for child in proposal.children if child.task_type is TaskType.WORKER)
        if len(workers) != 2:
            raise ProductionCompositionError(
                "Phase 8 production shape requires exactly two Worker children"
            )
        if any(child.suggested_owner != "worker" for child in workers):
            raise ProductionCompositionError(
                "Phase 8 Worker children must remain worker-owned"
            )
        if any(child.dependencies for child in workers):
            raise ProductionCompositionError(
                "Phase 8 Worker children must be independent and have no dependencies"
            )
        continuations = tuple(child for child in proposal.children if child.child_key == "continuation")
        if len(continuations) != 1:
            raise ProductionCompositionError(
                "Phase 8 production shape requires exactly one child_key=continuation"
            )
        continuation = continuations[0]
        if continuation.task_type is not TaskType.DETERMINISTIC:
            raise ProductionCompositionError(
                "Phase 8 continuation must use task_type=deterministic"
            )
        if continuation.suggested_owner != "codex":
            raise ProductionCompositionError(
                "Phase 8 continuation must be suggested_owner=codex"
            )
        worker_keys = {child.child_key for child in workers}
        if set(continuation.dependencies) != worker_keys:
            raise ProductionCompositionError(
                "Phase 8 continuation dependencies must exactly match both Worker children"
            )
        if set(continuation.dependency_types) != worker_keys or any(
            continuation.dependency_types.get(key) is not PlannerDependencyType.CODE_INTEGRATED
            for key in worker_keys
        ):
            raise ProductionCompositionError(
                "Phase 8 continuation dependency types must be CODE_INTEGRATED for both Workers"
            )

    @staticmethod
    def _unload_local_provider_models(
        *provider_maps: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Release idle local model managers after a terminal root.

        Provider requests already release their active-request lease.  The
        root terminal boundary owns the next step: ask each existing local
        ``OllamaModelManager`` to unload idle models without introducing a
        second lifecycle registry.  Cleanup is projected as bounded evidence;
        a cleanup failure must not rewrite an already-completed root result.
        """

        seen: set[tuple[int, str]] = set()
        unloaded: set[str] = set()
        errors: list[dict[str, str]] = []
        for provider_map in provider_maps:
            if not isinstance(provider_map, Mapping):
                continue
            for provider in provider_map.values():
                manager = getattr(provider, "model_manager", None)
                unload_idle = getattr(manager, "unload_idle", None)
                model = getattr(provider, "model", None)
                model_name = model.strip() if isinstance(model, str) and model.strip() else "unknown"
                if not callable(unload_idle):
                    continue
                identity = (id(manager), model_name)
                if identity in seen:
                    continue
                seen.add(identity)
                try:
                    names = unload_idle(deadline_seconds=30.0)
                except Exception as exc:  # lifecycle observability must stay bounded
                    category = getattr(exc, "category", None)
                    if not isinstance(category, str) or not category.strip() or len(category) > 128:
                        category = "lifecycle_failure"
                    errors.append({"model": model_name, "category": category.strip()})
                    continue
                if isinstance(names, (str, bytes)) or not isinstance(names, Sequence):
                    continue
                for name in names:
                    if isinstance(name, str) and name.strip() and len(name.strip()) <= 128:
                        unloaded.add(name.strip())
        return {
            "unloaded_models": sorted(unloaded),
            "errors": errors,
        }

    def submit(self, objective: str, **kwargs: Any) -> dict[str, Any]:
        """Submit one fresh root and run one bounded existing execution pass."""

        from src.dev_agent.operation import OperationService
        from src.dev_agent.operation_runtime import RuntimeCoordinator

        root = OperationService.submit(self.operation_config, objective, **kwargs)
        proposal = self.planner(root)
        if not isinstance(proposal, RootPlanningProposal):
            raise ProductionCompositionError("Planner boundary must return a RootPlanningProposal")
        if proposal.parent_task_id != root.task_id:
            raise ProductionCompositionError("Planner proposal parent identity does not match the fresh root")
        self.validate_phase8_root_shape(proposal)
        run_id = f"phase8-{uuid4().hex}"
        base_revision = resolved_revision(self.repository, self.target_ref)
        raw_specs = self.task_specs(root, proposal)
        if not isinstance(raw_specs, Mapping):
            raise ProductionCompositionError("task-spec boundary must return a mapping")
        expected_keys = {child.child_key for child in proposal.children}
        if set(raw_specs) != expected_keys:
            raise ProductionCompositionError("task-spec boundary must cover the exact Planner child set")

        with OperationService.open(self.operation_config) as operation:
            parent = operation.store.load_task(root.task_id)
            if parent is None:
                raise ProductionCompositionError("fresh Operation root is not durable")
            operation.validate_planning_proposal(proposal)
            operation.apply_planning_proposal(proposal, execution_owner="devfarm")
            operation.park_planning_root_for_children(
                parent_task_id=root.task_id,
                proposal_id=proposal.proposal_id,
            )
            candidate = DevelopmentPlanningBridge(self.repository).build_candidate(
                parent,
                proposal,
                run_id=run_id,
                base_revision=base_revision,
                task_specs=raw_specs,
            )
            for manifest_path, manifest in candidate.manifests:
                write_manifest(self.repository, manifest)
            create_plan(self.repository, candidate.plan)
            commander = CodexSupervisedCommanderRun(self.repository, run_id)
            commander.create(roadmap_reference=self.roadmap_reference)
            bindings = self._worker_bindings(commander.plan())
            operation.handoff_planning_children_to_devfarm(
                proposal_id=proposal.proposal_id,
                run_id=run_id,
                bindings=bindings,
            )
            provider_map = self.providers(bindings)
            if not isinstance(provider_map, Mapping):
                raise ProductionCompositionError("provider boundary must return a mapping")
            fallback_provider_map = None
            if self.fallback_providers is not None:
                fallback_provider_map = self.fallback_providers(bindings)
                if not isinstance(fallback_provider_map, Mapping):
                    raise ProductionCompositionError("fallback provider boundary must return a mapping")
            executor = Phase8ProductionExecutor(
                operation=operation,
                commander=commander,
                proposal_id=proposal.proposal_id,
                devfarm_run_id=run_id,
                bindings=bindings,
                providers=provider_map,
                fallback_providers=fallback_provider_map,
                orchestrator=self.orchestrator,
                review_proposal=self.review_proposal,
                final_review_decision=self.final_review_decision,
                target_checkout=self.target_checkout,
                target_ref=self.target_ref,
                verification_trust_level=self.verification_trust_level,
                operator_approved=self.operator_approved,
                dispatch_timeout_seconds=self.dispatch_timeout_seconds,
                local_trial=self.local_trial,
            )
            execution = executor.advance()
            continuation_changes = [
                item
                for item in execution.get("operation_changes", [])
                if item.get("planner_child_key") not in bindings
                and item.get("state") in {"queued", "waiting_dependency"}
            ]
            if len(continuation_changes) != 1:
                raise ProductionCompositionError(
                    "production submission requires exactly one released continuation"
                )
            continuation_task_id = continuation_changes[0]["task_id"]

        # The continuation is the only newly claimable Operation item after
        # the DevFarm-owned children integrate.  Reuse one existing
        # RuntimeCoordinator cycle to execute it; this is deliberately not a
        # serve loop or a second scheduler.
        with RuntimeCoordinator.open(
            self.operation_config,
            revision=base_revision,
            instance_id=f"{run_id}-runtime",
        ) as runtime:
            continuation = runtime.run_once()
            if continuation is None or continuation.task_id != continuation_task_id:
                raise ProductionCompositionError(
                    "RuntimeCoordinator did not execute the released continuation"
                )
            if continuation.status.value != "completed":
                raise ProductionCompositionError("released continuation did not reach terminal completion")
            root_terminal = runtime.operation.complete_planning_root(
                parent_task_id=root.task_id,
                proposal_id=proposal.proposal_id,
                continuation_task_id=continuation.task_id,
            )
        local_model_cleanup = self._unload_local_provider_models(
            provider_map,
            fallback_provider_map,
        )
        result = {
            "root_task_id": root.task_id,
            "run_id": run_id,
            "proposal_id": proposal.proposal_id,
            "execution": {
                "commander_status": execution.get("commander_status"),
                "reviewed": list(execution.get("reviewed", [])),
                "integrated": list(execution.get("integrated", [])),
                "repaired": list(execution.get("repaired", [])),
                "fallback": list(execution.get("fallback", [])),
                "continuation_ready": bool(execution.get("continuation_ready")),
                "continuation_task_id": continuation.task_id,
                "continuation_state": continuation.status.value,
                "root_state": root_terminal.status.value,
                "local_model_cleanup": local_model_cleanup,
            },
        }
        projected = _bounded_projection(result)
        if not isinstance(projected, dict):
            raise ProductionCompositionError("Phase 8 submission result is not bounded")
        return projected

    def observe(self, run_id: str, **_: Any) -> dict[str, Any]:
        """Read one durable Commander projection without advancing it."""

        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id must be a bounded non-empty string")
        plan = CodexSupervisedCommanderRun(self.repository, run_id.strip()).plan()
        workers = [
            {
                key: task.get(key)
                for key in ("task_id", "planner_child_key", "owner", "status", "last_attempt_id", "integration_revision")
                if task.get(key) is not None
            }
            for task in plan.get("tasks", [])
            if isinstance(task, Mapping) and task.get("owner") == "worker"
        ]
        proposal_ids = {
            task.get("planning_proposal_id")
            for task in plan.get("tasks", [])
            if isinstance(task, Mapping) and isinstance(task.get("planning_proposal_id"), str)
        }
        if len(proposal_ids) != 1:
            raise ProductionCompositionError("Commander plan has no unique Planner proposal identity")
        result = {
            "run_id": run_id.strip(),
            "proposal_id": next(iter(proposal_ids)),
            "commander_status": plan.get("status"),
            "workers": workers,
        }
        projected = _bounded_projection(result)
        if not isinstance(projected, dict):
            raise ProductionCompositionError("Phase 8 observation is not bounded")
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
        fallback_providers: Mapping[str, Any] | None = None,
        orchestrator: Any,
        review_proposal: Callable[[Mapping[str, Any]], Mapping[str, Any]],
        final_review_decision: Callable[[Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any]],
        target_checkout: str | Path,
        target_ref: str = "HEAD",
        verification_trust_level: str = "TRUSTED_HOST_EXEC",
        operator_approved: bool = True,
        dispatch_timeout_seconds: int | float = 300.0,
        local_trial: bool = False,
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
        if not isinstance(local_trial, bool):
            raise TypeError("local_trial must be a boolean")
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
        self.fallback_providers = dict(fallback_providers or {})
        self.orchestrator = orchestrator
        self.review_proposal = review_proposal
        self.final_review_decision = final_review_decision
        self.target_checkout = str(Path(target_checkout))
        self.target_ref = target_ref.strip()
        self.verification_trust_level = verification_trust_level
        self.operator_approved = operator_approved
        self.dispatch_timeout_seconds = float(dispatch_timeout_seconds)
        self.local_trial = local_trial

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

    def _repair_failed_proposals(self, plan: Mapping[str, Any]) -> list[str]:
        """Reassign one bounded model-output failure through existing repair seams.

        This is deliberately one correction pass, not a retry loop.  Only a
        durable ``WORKER_OUTPUT_*`` proposal failure is eligible; provider,
        transport, UNKNOWN, and reconciliation states remain outside model
        repair.  ``reassign`` creates the fresh immutable manifest and keeps
        the rejected attempt as evidence.
        """

        rework_handoff = getattr(self.commander, "rework_handoff", None)
        reassign = getattr(self.commander, "reassign", None)
        if not callable(rework_handoff) or not callable(reassign):
            return []
        repaired: list[str] = []
        for task in self._worker_tasks(plan):
            if task.get("status") != "REJECTED" or task.get("block_reason") != "proposal_failed":
                continue
            # A manifest history means this task already consumed the bounded
            # automatic correction opportunity.  Do not blind-retry it on a
            # later executor/resume call.
            if isinstance(task.get("manifest_history"), list) and task.get("manifest_history"):
                continue
            error = task.get("last_error")
            if not isinstance(error, str) or not error.startswith("WORKER_OUTPUT_"):
                continue
            task_id = task.get("task_id")
            attempt_id = task.get("last_attempt_id")
            provider = self.providers.get(task_id) if isinstance(task_id, str) else None
            provider_id = getattr(provider, "provider_id", None)
            model_id = getattr(provider, "model_id", None) or getattr(provider, "model", None)
            binding_id = getattr(provider, "provider_binding_id", None)
            if not all(isinstance(value, str) and value.strip() for value in (task_id, attempt_id, provider_id, model_id)):
                continue
            try:
                _manifest_path, manifest = load_worker_manifest(self.commander.root, task)
                failure_spec = build_concrete_failure_spec(error, manifest=manifest)
                directive = RepairDirective.from_failure_spec(failure_spec)
                result_ref = task.get("result_ref")
                if not isinstance(result_ref, str) or not result_ref.strip():
                    raise ProductionCompositionError("proposal failure has no durable result reference")
                handoff = rework_handoff(
                    task_id,
                    failure_evidence_reference={
                        "kind": "worker_proposal_failure",
                        "path": result_ref,
                        "attempt_id": attempt_id,
                    },
                    review_findings_reference=None,
                    required_correction=directive.required_action,
                    failure_spec=failure_spec,
                    repair_directive=directive,
                    repair_context={
                        "source_attempt_id": attempt_id,
                        "source_failure_signature": failure_spec.failure_signature,
                        "latest_failure_spec": failure_spec.to_dict(),
                        "latest_repair_directive": directive.to_dict(),
                        "current_repair": directive.to_dict(),
                        "historical_constraints": {
                            "unresolved": [directive.required_action],
                            "resolved_must_not_regress": [],
                        },
                        "directive_rebound": True,
                    },
                )
                reassign(
                    task_id,
                    provider_id=provider_id.strip(),
                    model_id=model_id.strip(),
                    provider_binding_id=binding_id if isinstance(binding_id, str) and binding_id.strip() else None,
                    rework_handoff=handoff,
                )
            except (ProductionCompositionError, ValueError, TypeError) as exc:
                raise ProductionCompositionError(
                    f"bounded Worker repair handoff failed for {task.get('planner_child_key', 'worker')}"
                ) from exc
            repaired.append(str(task.get("planner_child_key")))
        return repaired

    def _fallback_failed_proposals(self, plan: Mapping[str, Any]) -> list[str]:
        """Rebind one recurrent proposal failure to the configured fallback.

        The fallback is deliberately optional and per-task. It is considered
        only after the primary model consumed one concrete correction attempt,
        and it receives a fresh handoff built from the latest failure and
        manifest. Provider/runtime, UNKNOWN, and reconciliation failures do
        not enter this path.
        """

        if not self.fallback_providers:
            return []
        rework_handoff = getattr(self.commander, "rework_handoff", None)
        reassign = getattr(self.commander, "reassign", None)
        if not callable(rework_handoff) or not callable(reassign):
            return []
        fallback: list[str] = []
        for task in self._worker_tasks(plan):
            if task.get("status") != "REJECTED" or task.get("block_reason") != "proposal_failed":
                continue
            history = task.get("manifest_history")
            if not isinstance(history, list) or not history:
                continue
            error = task.get("last_error")
            if not isinstance(error, str) or not error.startswith("WORKER_OUTPUT_"):
                continue
            task_id = task.get("task_id")
            attempt_id = task.get("last_attempt_id")
            if (
                not isinstance(task_id, str)
                or not task_id.strip()
                or not isinstance(attempt_id, str)
                or not attempt_id.strip()
            ):
                continue
            current_provider = self.providers.get(task_id)
            fallback_provider = self.fallback_providers.get(task_id)
            if fallback_provider is None:
                continue
            provider_id = getattr(fallback_provider, "provider_id", None)
            model_id = getattr(fallback_provider, "model_id", None) or getattr(fallback_provider, "model", None)
            binding_id = getattr(fallback_provider, "provider_binding_id", None)
            current_provider_id = getattr(current_provider, "provider_id", None)
            current_model_id = getattr(current_provider, "model_id", None) or getattr(current_provider, "model", None)
            current_binding_id = getattr(current_provider, "provider_binding_id", None)
            if not all(isinstance(value, str) and value.strip() for value in (provider_id, model_id)):
                continue
            if (
                isinstance(current_provider_id, str)
                and current_provider_id.strip() == provider_id.strip()
                and isinstance(current_model_id, str)
                and current_model_id.strip() == model_id.strip()
                and (current_binding_id or current_provider_id) == (binding_id or provider_id)
            ):
                continue
            attempt_count = task.get("attempt_count")
            max_attempts = task.get("max_attempts")
            if isinstance(attempt_count, int) and isinstance(max_attempts, int) and attempt_count >= max_attempts:
                continue
            try:
                _manifest_path, manifest = load_worker_manifest(self.commander.root, task)
                failure_spec = build_concrete_failure_spec(error, manifest=manifest)
                directive = RepairDirective.from_failure_spec(failure_spec)
                result_ref = task.get("result_ref")
                if not isinstance(result_ref, str) or not result_ref.strip():
                    raise ProductionCompositionError("proposal failure has no durable result reference")
                handoff = rework_handoff(
                    task_id,
                    failure_evidence_reference={
                        "kind": "worker_proposal_failure",
                        "path": result_ref,
                        "attempt_id": attempt_id,
                    },
                    review_findings_reference=None,
                    required_correction=directive.required_action,
                    failure_spec=failure_spec,
                    repair_directive=directive,
                    repair_context={
                        "source_attempt_id": attempt_id,
                        "source_failure_signature": failure_spec.failure_signature,
                        "latest_failure_spec": failure_spec.to_dict(),
                        "latest_repair_directive": directive.to_dict(),
                        "current_repair": directive.to_dict(),
                        "historical_constraints": {
                            "unresolved": [directive.required_action],
                            "resolved_must_not_regress": [],
                        },
                        "directive_rebound": True,
                        "fallback_from_provider": current_provider_id,
                        "fallback_to_provider": provider_id.strip(),
                    },
                )
                reassign(
                    task_id,
                    provider_id=provider_id.strip(),
                    model_id=model_id.strip(),
                    provider_binding_id=binding_id if isinstance(binding_id, str) and binding_id.strip() else None,
                    rework_handoff=handoff,
                )
            except (DevFarmError, ProductionCompositionError, ValueError, TypeError) as exc:
                raise ProductionCompositionError(
                    f"bounded Worker fallback handoff failed for {task.get('planner_child_key', 'worker')}"
                ) from exc
            self.providers[task_id] = fallback_provider
            fallback.append(str(task.get("planner_child_key")))
        return fallback

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
            local_trial=self.local_trial,
        )
        plan = self.commander.plan()
        repaired = self._repair_failed_proposals(plan)
        if repaired:
            # One fresh correction pass is enough to consume the concrete
            # directive.  Further recurrence remains a durable failure for
            # the existing refinement/fallback authority to classify.
            step = self.commander.advance(
                providers=self.providers,
                orchestrator=self.orchestrator,
                verification_trust_level=self.verification_trust_level,
                operator_approved=self.operator_approved,
                dispatch_timeout_seconds=self.dispatch_timeout_seconds,
                local_trial=self.local_trial,
            )
            plan = self.commander.plan()
        fallback = self._fallback_failed_proposals(plan)
        if fallback:
            # The fallback receives a fresh immutable manifest and the latest
            # concrete directive. It is one bounded alternate-model pass.
            step = self.commander.advance(
                providers=self.providers,
                orchestrator=self.orchestrator,
                verification_trust_level=self.verification_trust_level,
                operator_approved=self.operator_approved,
                dispatch_timeout_seconds=self.dispatch_timeout_seconds,
                local_trial=self.local_trial,
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
            "repaired": repaired,
            "fallback": fallback,
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


__all__ = [
    "Phase8ProductionComposition",
    "Phase8ProductionExecutor",
    "Phase8ProductionSubmission",
    "ProductionCompositionError",
]
