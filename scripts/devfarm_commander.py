"""Development-only parent-plan coordination for the existing DevFarm.

The Commander stores a small JSON plan above the existing manifest, proposal,
and host-verification boundaries.  It does not replace the production Queue,
Scheduler, authority checks, or Worker execution engine.  A plan is durable so
Codex can reconstruct ownership and progress after a conversation or process
restart, while official-branch integration remains an explicit review action.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence
import uuid

from scripts.devfarm import DevFarmError, init_farm, validate_manifest, validate_result
from scripts.devfarm_manifests import load_worker_manifest
from scripts.devfarm_orchestrator import DevFarmOrchestrator, WorkerAssignment
from scripts import devfarm_plan_validation as plan_validation
from scripts.devfarm_repository import read_json, repository_path, resolved_revision
from scripts.devfarm_plan_queries import result_reference
from src.dev_agent.providers.base import ModelProvider


PLAN_SCHEMA_VERSION = plan_validation.PLAN_SCHEMA_VERSION
_PLAN_STATUSES = plan_validation.PLAN_STATUSES
_OWNERS = plan_validation.OWNERS
_DEPENDENCY_COMPLETE = plan_validation.DEPENDENCY_COMPLETE
_DEPENDENCY_FAILURE = plan_validation.DEPENDENCY_FAILURE
_ACTIVE_TASK_STATUSES = plan_validation.ACTIVE_TASK_STATUSES

# These local names preserve the Commander implementation's private internal
# vocabulary while the repository/Git behavior itself lives in one public
# development-only boundary.  Other DevFarm modules import the public names,
# never these aliases.
_read_json = read_json
_repository_path = repository_path
_resolved_revision = resolved_revision
_text = plan_validation.text
_plan_id = plan_validation.plan_id
_paths = plan_validation.paths
_ownership_conflict = plan_validation.ownership_conflict
validate_plan = plan_validation.validate_plan
summarize_delegation = plan_validation.summarize_delegation


class PlanConflictError(DevFarmError):
    """A Commander plan changed after a caller loaded its revision."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def refresh_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    """Release dependency-ready tasks without dispatching or mutating code."""

    plan = deepcopy(validate_plan(value))
    by_id = {task["task_id"]: task for task in plan["tasks"]}
    changed = True
    while changed:
        changed = False
        for task in plan["tasks"]:
            dependency_statuses = [by_id[item]["status"] for item in task["dependencies"]]
            if task["status"] == "BLOCKED" and task.get("block_reason") == "dependency_failed":
                if all(status in _DEPENDENCY_COMPLETE for status in dependency_statuses):
                    task["status"] = "READY"
                    task.pop("block_reason", None)
                    changed = True
                continue
            if task["status"] != "PLANNED":
                continue
            if any(status in _DEPENDENCY_FAILURE for status in dependency_statuses):
                task["status"] = "BLOCKED"
                task["block_reason"] = "dependency_failed"
                changed = True
            elif all(status in _DEPENDENCY_COMPLETE for status in dependency_statuses):
                task["status"] = "READY"
                changed = True
    statuses = [task["status"] for task in plan["tasks"]]
    if statuses and all(status in {"INTEGRATED", "SUPERSEDED"} for status in statuses) and "SUPERSEDED" in statuses:
        plan["status"] = "SUPERSEDED"
    elif statuses and all(status == "INTEGRATED" for status in statuses):
        plan["status"] = "INTEGRATED"
    elif "DISPATCHED" in statuses:
        plan["status"] = "DISPATCHED"
    elif "PROPOSED" in statuses:
        plan["status"] = "PROPOSED"
    elif "HOST_VERIFIED" in statuses:
        plan["status"] = "HOST_VERIFIED"
    elif "READY" in statuses:
        plan["status"] = "READY"
    elif "BLOCKED" in statuses:
        plan["status"] = "BLOCKED"
    elif "REJECTED" in statuses:
        plan["status"] = "REJECTED"
    else:
        plan["status"] = "PLANNED"
    plan["updated_at"] = _now()
    return plan


class CommanderPlanStore:
    """Atomic and optimistic-CAS persistence for development-only plans."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.directory = init_farm(self.root) / "plans"
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, run_id: str) -> Path:
        plan_id = _plan_id(run_id)
        path = self.directory / f"{plan_id}.json"
        if path.is_symlink():
            raise DevFarmError("Commander plan path cannot be a symlink")
        return path

    @staticmethod
    @contextmanager
    def _lock(path: Path):
        """Hold a short-lived OS lock while comparing and replacing a plan.

        The lock file itself is harmless development metadata.  OS-level
        locking means a crashed process releases the lock without requiring a
        stale lock-file deletion heuristic; the JSON plan remains the source
        of truth and the revision check prevents last-write-wins updates.
        """

        lock_path = path.with_name(path.name + ".lock")
        handle = lock_path.open("a+b")
        locked = False
        try:
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise PlanConflictError(f"Commander plan is being modified: {path.name}") from exc
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise PlanConflictError(f"Commander plan is being modified: {path.name}") from exc
            locked = True
            yield
        finally:
            if locked:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def create(self, value: Mapping[str, Any]) -> dict[str, Any]:
        plan = refresh_plan(validate_plan(value, root=self.root))
        path = self.path_for(plan["run_id"])
        # Serialize the read/check/write window across independent plan files;
        # per-plan CAS locks alone cannot prevent two simultaneous plans from
        # both observing the same path as available.
        with self._lock(self.directory / ".ownership"):
            with self._lock(path):
                if path.exists():
                    raise FileExistsError(path)
                conflicts = self.active_ownership_conflicts(plan)
                if conflicts:
                    first = conflicts[0]
                    raise DevFarmError(
                        "active ownership conflict: "
                        f"{first['existing_run_id']}/{first['existing_task_id']} owns "
                        f"{first['existing_path']} overlapping "
                        f"{first['run_id']}/{first['task_id']}:{first['path']}"
                    )
                self._write(plan)
        return plan

    def load(self, run_id: str) -> dict[str, Any]:
        path = self.path_for(run_id)
        if not path.is_file():
            raise DevFarmError(f"Commander plan does not exist: {run_id}")
        return validate_plan(_read_json(path), root=self.root)

    def save(self, value: Mapping[str, Any], *, expected_revision: int | None = None) -> dict[str, Any]:
        plan = validate_plan(value, root=self.root)
        plan["updated_at"] = _now()
        path = self.path_for(plan["run_id"])
        expected = plan["plan_revision"] if expected_revision is None else expected_revision
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise DevFarmError("expected_revision must be a non-negative integer")
        with self._lock(path):
            if not path.is_file():
                raise DevFarmError(f"Commander plan does not exist: {plan['run_id']}")
            current = validate_plan(_read_json(path), root=self.root)
            actual = current["plan_revision"]
            if actual != expected:
                raise PlanConflictError(
                    f"Commander plan revision conflict: expected {expected}, actual {actual}"
                )
            plan["plan_revision"] = expected + 1
            self._write(plan)
        return plan

    def list(self) -> list[dict[str, Any]]:
        return [self.load(path.stem) for path in sorted(self.directory.glob("*.json")) if not path.is_symlink()]

    def _plans_for_ownership(self) -> list[dict[str, Any]]:
        """Load plans for conflict checks without releasing legacy ownership.

        A plan can become unreadable under a deliberately tightened manifest
        policy, for example when an older Worker manifest owns a path that is
        now protected.  Such a historical plan must not be silently ignored:
        its raw, validated path ownership still reserves the checkout.  Keep
        the normal ``load`` path strict for status/dispatch, but use this
        narrow projection for cross-plan ownership checks so unrelated new
        plans can be created safely.
        """

        plans: list[dict[str, Any]] = []
        for path in sorted(self.directory.glob("*.json")):
            if path.is_symlink():
                continue
            try:
                plans.append(self.load(path.stem))
            except DevFarmError as validation_error:
                try:
                    raw = _read_json(path)
                    if not isinstance(raw, Mapping) or raw.get("schema_version", PLAN_SCHEMA_VERSION) != PLAN_SCHEMA_VERSION:
                        raise DevFarmError("legacy plan projection is invalid")
                    run_id = _plan_id(raw.get("run_id"))
                    raw_tasks = raw.get("tasks")
                    if not isinstance(raw_tasks, list) or not raw_tasks:
                        raise DevFarmError("legacy plan projection has no tasks")
                    projected_tasks: list[dict[str, Any]] = []
                    for raw_task in raw_tasks:
                        if not isinstance(raw_task, Mapping):
                            raise DevFarmError("legacy plan task projection is invalid")
                        task_id = _text(raw_task.get("task_id"), "legacy task_id", max_length=101)
                        owner = _text(raw_task.get("owner"), "legacy task owner", max_length=16).lower()
                        if owner not in _OWNERS:
                            raise DevFarmError("legacy plan task owner is invalid")
                        status = _text(raw_task.get("status"), "legacy task status", max_length=32).upper()
                        if status not in _PLAN_STATUSES:
                            raise DevFarmError("legacy plan task status is invalid")
                        if status not in _ACTIVE_TASK_STATUSES:
                            continue
                        projected_tasks.append(
                            {
                                "task_id": task_id,
                                "owner": owner,
                                "status": status,
                                "ownership": _paths(raw_task.get("ownership", []), "legacy ownership paths"),
                            }
                        )
                    plans.append({"run_id": run_id, "tasks": projected_tasks})
                except (TypeError, ValueError, DevFarmError):
                    # An unreadable plan whose ownership cannot be safely
                    # projected remains a hard error; never fail open.
                    raise validation_error
        return plans

    def active_ownership_conflicts(self, plan: Mapping[str, Any]) -> list[dict[str, str]]:
        """Return path conflicts with unfinished plans before a new plan is saved.

        Ownership is still a Commander concern: this check does not schedule,
        claim, or retry work.  It closes the gap between the per-plan overlap
        check and two independent active plans targeting the same checkout.
        Terminal task ownership is released only after integration or explicit
        supersession has been durably recorded.
        """

        candidate = validate_plan(plan, root=self.root)
        candidate_paths = [
            (task["task_id"], path)
            for task in candidate["tasks"]
            if task["status"] in _ACTIVE_TASK_STATUSES
            for path in task["ownership"]
        ]
        if not candidate_paths:
            return []
        conflicts: list[dict[str, str]] = []
        for existing in self._plans_for_ownership():
            if existing["run_id"] == candidate["run_id"]:
                continue
            for existing_task in existing["tasks"]:
                if existing_task["status"] not in _ACTIVE_TASK_STATUSES:
                    continue
                for existing_path in existing_task["ownership"]:
                    for task_id, path in candidate_paths:
                        if _ownership_conflict(existing_path, path):
                            conflicts.append(
                                {
                                    "existing_run_id": existing["run_id"],
                                    "existing_task_id": existing_task["task_id"],
                                    "existing_path": existing_path,
                                    "run_id": candidate["run_id"],
                                    "task_id": task_id,
                                    "path": path,
                                }
                            )
        return conflicts

    def active_ownership(self, *, run_id: str | None = None) -> list[dict[str, Any]]:
        """Return the durable file ownership projection for active plan work.

        Commander remains the sole owner of Task ownership.  This is a
        read-only query for supervisors and operators: it does not claim a
        path, create a lock, or change readiness.  Rejected/blocked tasks are
        included because their ownership remains reserved until integration,
        supersession, or an explicit recovery action is recorded.
        """

        selected = [self.load(run_id)] if run_id is not None else self._plans_for_ownership()
        records: list[dict[str, Any]] = []
        for plan in selected:
            for task in plan["tasks"]:
                if task["status"] not in _ACTIVE_TASK_STATUSES:
                    continue
                for path in task["ownership"]:
                    record: dict[str, Any] = {
                        "run_id": plan["run_id"],
                        "task_id": task["task_id"],
                        "owner": task["owner"],
                        "status": task["status"],
                        "path": path,
                    }
                    if task.get("work_address") is not None:
                        record["work_address"] = task["work_address"]
                    records.append(record)
        records.sort(key=lambda item: (item["path"], item["run_id"], item["task_id"]))
        return records

    def _write(self, plan: Mapping[str, Any]) -> None:
        normalized = validate_plan(plan, root=self.root)
        target = self.path_for(normalized["run_id"])
        temporary = self.directory / f".{normalized['run_id']}.{uuid.uuid4().hex}.tmp"
        temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)


def _task(plan: Mapping[str, Any], task_id: str) -> dict[str, Any]:
    for task in plan["tasks"]:
        if task["task_id"] == task_id:
            return task
    raise DevFarmError(f"Commander task does not exist: {task_id}")


_manifest_for = load_worker_manifest


_result_ref = result_reference


def _record_result(
    plan: dict[str, Any],
    task_id: str,
    stage: str,
    status: str,
    result_ref: str | None = None,
    *,
    attempt_id: str | None = None,
) -> None:
    for record in plan["results"]:
        if record["task_id"] == task_id and record["stage"] == stage and record.get("attempt_id") == attempt_id:
            record["status"] = status
            if result_ref is not None:
                record["result_ref"] = result_ref
            record["recorded_at"] = _now()
            return
    record: dict[str, Any] = {
        "task_id": task_id,
        "stage": stage,
        "status": status,
        "recorded_at": _now(),
    }
    if result_ref is not None:
        record["result_ref"] = result_ref
    if attempt_id is not None:
        record["attempt_id"] = _text(attempt_id, "attempt_id", max_length=101)
    plan["results"].append(record)


def record_result(
    plan: dict[str, Any],
    task_id: str,
    stage: str,
    status: str,
    result_ref: str | None = None,
    *,
    attempt_id: str | None = None,
) -> None:
    """Public plan-result recording boundary for Supervisor composition."""

    _record_result(plan, task_id, stage, status, result_ref, attempt_id=attempt_id)


def _apply_proposal_result(plan: dict[str, Any], task: dict[str, Any], result: Mapping[str, Any]) -> None:
    status = str(result.get("status", "failed"))
    attempt_id = result.get("attempt_id")
    result_ref = _result_ref(task["task_id"], attempt_id if isinstance(attempt_id, str) and attempt_id.strip() else None)
    task["result_ref"] = result_ref
    if attempt_id is not None:
        task["last_attempt_id"] = _text(attempt_id, "attempt_id", max_length=101)
    task["last_result_status"] = status
    task.pop("stale_result_attempt_id", None)
    if status == "completed" and result.get("changed_files"):
        task["status"] = "PROPOSED"
    elif status == "blocked_external":
        task["status"] = "BLOCKED"
        task["block_reason"] = "provider_unavailable"
    else:
        task["status"] = "REJECTED"
        task["block_reason"] = "proposal_failed"
    _record_result(plan, task["task_id"], "proposal", status, result_ref, attempt_id=task.get("last_attempt_id"))


def create_plan(root: str | Path, value: Mapping[str, Any]) -> dict[str, Any]:
    return CommanderPlanStore(root).create(value)


def list_active_ownership(root: str | Path, *, run_id: str | None = None) -> list[dict[str, Any]]:
    """Public read-only ownership query for Supervisor/operator boundaries."""

    return CommanderPlanStore(root).active_ownership(run_id=run_id)


def dispatch_plan(
    root: str | Path,
    run_id: str,
    *,
    providers: Mapping[str, ModelProvider],
    host_dispatches: Mapping[str, Any] | None = None,
    orchestrator: DevFarmOrchestrator | None = None,
    dispatch_timeout_seconds: int | float = 300.0,
) -> dict[str, Any]:
    """Dispatch all currently READY worker tasks through existing proposals."""

    root_path = Path(root).resolve()
    if isinstance(dispatch_timeout_seconds, bool) or not isinstance(dispatch_timeout_seconds, (int, float)):
        raise DevFarmError("dispatch_timeout_seconds must be numeric")
    if dispatch_timeout_seconds <= 0:
        raise DevFarmError("dispatch_timeout_seconds must be positive")
    store = CommanderPlanStore(root_path)
    plan = refresh_plan(store.load(run_id))
    # The plan baseline is a durable reference, not a lock on the mutable
    # repository checkout.  Codex may commit while older Worker manifests
    # continue to propose from their own validated commit object.
    _resolved_revision(root_path, plan["base_revision"])
    ready: list[tuple[dict[str, Any], Path, ModelProvider]] = []
    for task in plan["tasks"]:
        if task["status"] != "READY" or task["owner"] != "worker":
            continue
        manifest_path, manifest = _manifest_for(root_path, task)
        _resolved_revision(root_path, manifest["base_revision"])
        provider = providers.get(task["task_id"])
        if not isinstance(provider, ModelProvider):
            raise DevFarmError(f"no ModelProvider supplied for worker task: {task['task_id']}")
        if task["attempt_count"] >= task["max_attempts"]:
            task["status"] = "BLOCKED"
            task["block_reason"] = "attempt_limit_reached"
            continue
        ready.append((task, manifest_path, provider))
        task["status"] = "DISPATCHED"
        task["attempt_count"] += 1
        started_at = datetime.now(timezone.utc)
        task["dispatch_id"] = uuid.uuid4().hex
        task["dispatch_started_at"] = started_at.isoformat()
        task["dispatch_deadline_at"] = (
            started_at.timestamp() + float(dispatch_timeout_seconds)
        )
        task["dispatch_deadline_at"] = datetime.fromtimestamp(
            task["dispatch_deadline_at"], tz=timezone.utc
        ).isoformat()
        task["dispatch_owner_pid"] = os.getpid()
        task.pop("dispatch_recovery", None)
    plan = store.save(plan)
    if not ready:
        return refresh_plan(plan)
    farm = orchestrator or DevFarmOrchestrator()
    execution_assignments = [
        WorkerAssignment(
            manifest_path,
            provider,
            host_dispatch=(host_dispatches or {}).get(task["task_id"]),
        )
        for task, manifest_path, provider in ready
    ]
    try:
        proposals = farm.propose(root_path, execution_assignments)
    except Exception as exc:
        plan = store.load(run_id)
        for task, _manifest_path, _provider in ready:
            current = _task(plan, task["task_id"])
            current["status"] = "BLOCKED"
            current["block_reason"] = "proposal_dispatch_error"
            current["last_error"] = str(exc)[:1000]
        return store.save(refresh_plan(plan))
    plan = store.load(run_id)
    for task, result in zip((item[0] for item in ready), proposals):
        _apply_proposal_result(plan, _task(plan, task["task_id"]), result)
    return store.save(refresh_plan(plan))


def collect_plan(root: str | Path, run_id: str) -> dict[str, Any]:
    """Collect existing result artifacts without treating proposals as verified."""

    root_path = Path(root).resolve()
    store = CommanderPlanStore(root_path)
    plan = store.load(run_id)
    for task in plan["tasks"]:
        if task["owner"] != "worker":
            continue
        if task["status"] not in {"DISPATCHED", "PROPOSED"}:
            # Result collection is an in-flight reconciliation operation.
            # A terminal review decision, rejection, or reassign boundary
            # must not be overwritten by the mutable latest projection from
            # an earlier attempt.
            continue
        _manifest_path, manifest = _manifest_for(root_path, task)
        result_path = _repository_path(root_path, _result_ref(task["task_id"]), required_parent=".devfarm/results")
        if not result_path.is_file():
            continue
        result = validate_result(_read_json(result_path), manifest=manifest)
        attempt_id = result.get("attempt_id")
        stale_attempt_id = task.get("stale_result_attempt_id")
        if (
            isinstance(stale_attempt_id, str)
            and isinstance(attempt_id, str)
            and stale_attempt_id == attempt_id
        ):
            # A reassign/rework keeps the immutable old projection on disk.
            # It must not be mistaken for the new dispatch's result.
            continue
        if task["status"] == "READY" and task.get("last_attempt_id") is None:
            # A READY task has not yet established a new execution attempt.
            # The mutable latest projection is not authoritative for it.
            continue
        attempt_ref = _result_ref(task["task_id"], attempt_id if isinstance(attempt_id, str) and attempt_id.strip() else None)
        task["result_ref"] = attempt_ref
        if attempt_id is not None:
            task["last_attempt_id"] = _text(attempt_id, "attempt_id", max_length=101)
        task["last_result_status"] = result["status"]
        metrics = result.get("worker_metrics", {})
        host_verified = isinstance(metrics, Mapping) and metrics.get("host_verified") is True
        accepted = isinstance(metrics, Mapping) and metrics.get("result_accepted") is True
        verification_id = result.get("verification_id")
        has_verification_record = (
            isinstance(verification_id, str) and bool(verification_id.strip())
            and isinstance(attempt_id, str) and bool(attempt_id.strip())
            and (root_path / ".devfarm" / "results" / task["task_id"] / "attempts" / attempt_id / "verification" / f"{verification_id}.json").is_file()
        )
        if task["status"] != "INTEGRATED":
            if result["status"] == "completed" and host_verified and accepted and has_verification_record:
                task["status"] = "HOST_VERIFIED"
                patch_path = (
                    root_path
                    / ".devfarm"
                    / "results"
                    / task["task_id"]
                    / "attempts"
                    / str(attempt_id)
                    / "patch.diff"
                )
                if patch_path.is_file():
                    task["verified_patch_digest"] = hashlib.sha256(patch_path.read_bytes()).hexdigest()
                task.pop("block_reason", None)
                task.pop("last_error", None)
                _record_result(plan, task["task_id"], "host_verification", result["status"], attempt_ref, attempt_id=task.get("last_attempt_id"))
            elif result["status"] == "completed":
                task["status"] = "PROPOSED"
                _record_result(plan, task["task_id"], "proposal", result["status"], attempt_ref, attempt_id=task.get("last_attempt_id"))
            elif result["status"] == "blocked_external":
                task["status"] = "BLOCKED"
                task["block_reason"] = "provider_unavailable"
                _record_result(plan, task["task_id"], "proposal", result["status"], attempt_ref, attempt_id=task.get("last_attempt_id"))
            else:
                task["status"] = "REJECTED"
                task["block_reason"] = "proposal_failed"
                _record_result(plan, task["task_id"], "proposal", result["status"], attempt_ref, attempt_id=task.get("last_attempt_id"))
    return store.save(refresh_plan(plan))


def verify_plan(
    root: str | Path,
    run_id: str,
    *,
    task_ids: Sequence[str] | None = None,
    orchestrator: DevFarmOrchestrator | None = None,
    verification_trust_level: str = "STATIC_ONLY",
    operator_approved: bool = False,
) -> dict[str, Any]:
    """Host-verify PROPOSED tasks through the existing bounded verifier."""

    root_path = Path(root).resolve()
    store = CommanderPlanStore(root_path)
    plan = collect_plan(root_path, run_id)
    wanted = set(task_ids) if task_ids is not None else None
    selected = [
        task
        for task in plan["tasks"]
        if (
            task["owner"] == "worker"
            and (
                task["status"] == "PROPOSED"
                # A verifier boundary failure is a retryable host-side
                # condition.  Keep review/rework/rejection terminal states
                # out of collection, but allow the operator to rerun the
                # failed verification explicitly.
                or (
                    task["status"] == "REJECTED"
                    and task.get("block_reason") == "host_verification_failed"
                )
            )
            and (wanted is None or task["task_id"] in wanted)
        )
    ]
    if wanted is not None and any(item not in {task["task_id"] for task in selected} for item in wanted):
        missing = sorted(wanted - {task["task_id"] for task in selected})
        raise DevFarmError(f"tasks are not ready for host verification: {', '.join(missing)}")
    if not selected:
        return plan
    farm = orchestrator or DevFarmOrchestrator(
        verification_trust_level=verification_trust_level,
        operator_approved=operator_approved,
    )
    manifest_paths = [_manifest_for(root_path, task)[0] for task in selected]
    try:
        results = farm.verify(root_path, manifest_paths)
    except Exception as exc:
        # A verifier/provider boundary failure must become a durable rejected
        # attempt, not leave the parent Plan indefinitely PROPOSED.  The
        # bounded reassign path can then make an explicit next assignment.
        plan = store.load(run_id)
        for task in selected:
            current = _task(plan, task["task_id"])
            current["status"] = "REJECTED"
            current["last_result_status"] = "failed"
            current["block_reason"] = "host_verification_failed"
            current["last_error"] = str(exc)[:1000]
            _record_result(
                plan,
                current["task_id"],
                "host_verification",
                "failed",
                _result_ref(current["task_id"], current.get("last_attempt_id")),
                attempt_id=current.get("last_attempt_id"),
            )
        return store.save(refresh_plan(plan))
    plan = store.load(run_id)
    for task, result in zip(selected, results):
        current = _task(plan, task["task_id"])
        metrics = result.get("worker_metrics", {})
        accepted = result.get("status") == "completed" and isinstance(metrics, Mapping) and metrics.get("result_accepted") is True
        attempt_id = result.get("attempt_id")
        verification_id = result.get("verification_id")
        has_verification_record = (
            accepted
            and isinstance(verification_id, str) and bool(verification_id.strip())
            and isinstance(attempt_id, str) and bool(attempt_id.strip())
            and (root_path / ".devfarm" / "results" / current["task_id"] / "attempts" / attempt_id / "verification" / f"{verification_id}.json").is_file()
        )
        current["status"] = "HOST_VERIFIED" if has_verification_record else "REJECTED"
        current["last_result_status"] = result.get("status")
        if attempt_id is not None:
            current["last_attempt_id"] = _text(attempt_id, "attempt_id", max_length=101)
        if accepted:
            if current["status"] == "HOST_VERIFIED" and isinstance(attempt_id, str) and attempt_id.strip():
                patch_path = (
                    root_path
                    / ".devfarm"
                    / "results"
                    / current["task_id"]
                    / "attempts"
                    / attempt_id
                    / "patch.diff"
                )
                if patch_path.is_file():
                    current["verified_patch_digest"] = hashlib.sha256(patch_path.read_bytes()).hexdigest()
            current.pop("block_reason", None)
            current.pop("last_error", None)
        else:
            current["block_reason"] = "host_verification_failed"
            known_issues = result.get("known_issues")
            if isinstance(known_issues, Sequence) and not isinstance(known_issues, (str, bytes)):
                reasons = [
                    item.strip()
                    for item in known_issues
                    if isinstance(item, str) and item.strip()
                ]
                if reasons:
                    current["last_error"] = "; ".join(reasons)[:1000]
            current.setdefault("last_error", "host verification result was not accepted")
        _record_result(
            plan,
            current["task_id"],
            "host_verification",
            str(result.get("status", "failed")),
            _result_ref(current["task_id"], current.get("last_attempt_id")),
            attempt_id=current.get("last_attempt_id"),
        )
    return store.save(refresh_plan(plan))


def resume_plan(root: str | Path, run_id: str) -> dict[str, Any]:
    """Reconcile result artifacts and release dependency-ready tasks only."""

    return collect_plan(root, run_id)


def supersede_plan(root: str | Path, run_id: str, *, reason: str) -> dict[str, Any]:
    """Explicitly close a terminal plan and release its remaining ownership.

    Supersession is an operator action, not a retry or scheduler transition.
    In-flight or host-verified work is rejected so a caller cannot erase an
    unresolved external effect or bypass the review/integration boundary.
    Integrated tasks remain intact as durable history; every other task is
    marked ``SUPERSEDED`` and its ownership is consequently released.
    """

    root_path = Path(root).resolve()
    store = CommanderPlanStore(root_path)
    plan = store.load(run_id)
    normalized_reason = _text(reason, "reason", max_length=1000)
    if plan["status"] in {"INTEGRATED", "SUPERSEDED"}:
        raise DevFarmError(f"Commander plan is already terminal: {run_id}")
    in_flight = [
        task["task_id"]
        for task in plan["tasks"]
        if task["status"] in {"DISPATCHED", "PROPOSED", "HOST_VERIFIED"}
    ]
    if in_flight:
        raise DevFarmError(
            "cannot supersede in-flight or host-verified tasks: "
            + ", ".join(in_flight)
        )
    changed = False
    for task in plan["tasks"]:
        if task["status"] == "INTEGRATED":
            continue
        task["status"] = "SUPERSEDED"
        task["block_reason"] = "superseded"
        task["last_error"] = normalized_reason
        _record_result(plan, task["task_id"], "supersession", "superseded")
        changed = True
    if not changed:
        raise DevFarmError(f"Commander plan has no supersedable tasks: {run_id}")
    plan = refresh_plan(plan)
    return store.save(plan, expected_revision=plan["plan_revision"])


def _write_rework_manifest(
    root: Path,
    old_path: Path,
    manifest: Mapping[str, Any],
    rework_handoff: Mapping[str, Any],
) -> Path:
    """Write a new immutable manifest containing only the rework delta."""

    if not isinstance(rework_handoff, Mapping) or not rework_handoff:
        raise DevFarmError("rework_handoff must be a non-empty object")
    new_manifest = dict(manifest)
    new_manifest["rework_handoff"] = dict(rework_handoff)
    normalized = validate_manifest(new_manifest)
    new_path = old_path.with_name(f"{old_path.stem}.rework-{uuid.uuid4().hex[:12]}{old_path.suffix}")
    temporary = new_path.with_name(f".{new_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, new_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return new_path


def recover_orphaned_dispatches(
    root: str | Path,
    run_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Classify expired, result-less dispatches without replaying the effect.

    A dispatch deadline is durable evidence that the original owner may no
    longer be able to report.  Before that deadline this helper is strictly a
    no-op.  A result artifact, when present, is also left for ``collect_plan``
    to reconcile.  The helper never increments ``attempt_count`` and never
    starts another provider call.
    """

    root_path = Path(root).resolve()
    store = CommanderPlanStore(root_path)
    plan = store.load(run_id)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        raise DevFarmError("now must include a timezone")
    changed = False
    for task in plan["tasks"]:
        if task.get("owner") != "worker" or task.get("status") != "DISPATCHED":
            continue
        deadline_value = task.get("dispatch_deadline_at")
        if not isinstance(deadline_value, str):
            continue
        try:
            deadline = datetime.fromisoformat(deadline_value)
        except ValueError as exc:
            raise DevFarmError("dispatch_deadline_at must be an ISO-8601 timestamp") from exc
        if deadline.tzinfo is None or current_time < deadline:
            continue
        latest = _repository_path(
            root_path,
            _result_ref(task["task_id"]),
            required_parent=".devfarm/results",
        )
        attempt_id = task.get("last_attempt_id")
        attempt_result = None
        if isinstance(attempt_id, str) and attempt_id.strip():
            attempt_result = _repository_path(
                root_path,
                _result_ref(task["task_id"], attempt_id),
                required_parent=".devfarm/results",
            )
        latest_is_current = latest.is_file()
        stale_attempt_id = task.get("stale_result_attempt_id")
        if latest_is_current and isinstance(stale_attempt_id, str) and stale_attempt_id.strip():
            try:
                latest_value = _read_json(latest)
            except DevFarmError:
                latest_is_current = False
            else:
                latest_is_current = not (
                    isinstance(latest_value, Mapping)
                    and latest_value.get("attempt_id") == stale_attempt_id
                )
        if latest_is_current or (attempt_result is not None and attempt_result.is_file()):
            continue
        task["status"] = "BLOCKED"
        task["block_reason"] = "orphaned_dispatch"
        task["dispatch_recovery"] = "reconciliation_required"
        task["last_error"] = "dispatch deadline expired without a result artifact"
        _record_result(
            plan,
            task["task_id"],
            "dispatch_recovery",
            "blocked",
            attempt_id=attempt_id if isinstance(attempt_id, str) else None,
        )
        changed = True
    if not changed:
        return plan
    return store.save(refresh_plan(plan), expected_revision=plan["plan_revision"])


def reassign_task(
    root: str | Path,
    run_id: str,
    task_id: str,
    *,
    provider_id: str,
    model_id: str,
    provider_binding_id: str | None = None,
    rework_handoff: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    store = CommanderPlanStore(root)
    plan = store.load(run_id)
    task = _task(plan, task_id)
    if task["owner"] != "worker":
        raise DevFarmError("only worker tasks can be reassigned")
    if task["status"] not in {"REJECTED", "BLOCKED"}:
        raise DevFarmError("only rejected or blocked worker tasks can be reassigned")
    if task.get("dispatch_recovery") == "reconciliation_required":
        raise DevFarmError("orphaned dispatch requires explicit reconciliation before reassignment")
    if task["attempt_count"] >= task["max_attempts"]:
        raise DevFarmError("worker task attempt limit reached")
    old_manifest_path, old_manifest = _manifest_for(Path(root).resolve(), task)
    assignment: dict[str, Any] = {
        "task_id": task_id,
        "owner": "worker",
        "provider_id": _text(provider_id, "provider_id"),
        "model_id": _text(model_id, "model_id"),
    }
    if provider_binding_id is not None:
        assignment["provider_binding_id"] = _text(provider_binding_id, "provider_binding_id")
    task["assignment"] = assignment
    for item in plan["assignments"]:
        if item["task_id"] == task_id:
            item.clear()
            item.update(assignment)
            break
    task["status"] = "READY"
    if rework_handoff is not None:
        new_manifest_path = _write_rework_manifest(
            Path(root).resolve(),
            old_manifest_path,
            old_manifest,
            rework_handoff,
        )
        old_relative = task["manifest_path"]
        new_relative = new_manifest_path.relative_to(Path(root).resolve()).as_posix()
        task["manifest_path"] = new_relative
        history = list(task.get("manifest_history", []))
        if old_relative not in history:
            history.append(old_relative)
        if new_relative not in history:
            history.append(new_relative)
        task["manifest_history"] = history
    task.pop("block_reason", None)
    task.pop("last_error", None)
    previous_attempt_id = task.get("last_attempt_id")
    if isinstance(previous_attempt_id, str) and previous_attempt_id.strip():
        task["stale_result_attempt_id"] = previous_attempt_id
    for key in (
        "result_ref",
        "last_attempt_id",
        "last_result_status",
        "verified_patch_digest",
        "target_ref",
        "integration_revision",
        "source_attempt_id",
        "integration_note",
    ):
        task.pop(key, None)
    return store.save(refresh_plan(plan))


def verified_worker_patch(root: Path, task: Mapping[str, Any]) -> tuple[str, dict[str, Any], str]:
    """Backward-compatible read-only bridge to the Host integration service."""

    from scripts.devfarm_integration import verified_worker_patch as integration_verified_worker_patch

    return integration_verified_worker_patch(root, task)


def mark_integrated(
    root: str | Path,
    run_id: str,
    task_id: str,
    *,
    note: str,
    target_ref: str,
    integration_revision: str,
    source_attempt_id: str,
    verified_patch_digest: str,
) -> dict[str, Any]:
    """Backward-compatible bridge to the Host-owned integration service."""

    from scripts.devfarm_integration import integrate_worker

    return integrate_worker(
        root,
        run_id,
        task_id,
        note=note,
        target_ref=target_ref,
        integration_revision=integration_revision,
        source_attempt_id=source_attempt_id,
        verified_patch_digest=verified_patch_digest,
    )


def _cli_provider(provider_id: str, model_id: str, timeout_seconds: float) -> ModelProvider:
    # The provider runtime remains the only construction/activation boundary.
    # Keep this import local so importing the plan store never constructs one.
    from scripts.devfarm_provider_runtime import build_worker_provider

    return build_worker_provider(provider_id, model_id, timeout_seconds)


def build_cli_provider(provider_id: str, model_id: str, timeout_seconds: float) -> ModelProvider:
    """Public CLI composition boundary for an assigned development provider."""

    return _cli_provider(provider_id, model_id, timeout_seconds)


def dispatch_cli(
    root: str | Path,
    run_id: str,
    *,
    provider_id: str | None = None,
    model_id: str | None = None,
    timeout_seconds: float = 30.0,
    execution_boundary: str = "in_process",
) -> dict[str, Any]:
    if execution_boundary not in {"in_process", "host_process"}:
        raise DevFarmError("execution_boundary must be in_process or host_process")
    root_path = Path(root).resolve()
    plan = CommanderPlanStore(root_path).load(run_id)
    providers: dict[str, ModelProvider] = {}
    host_dispatches: dict[str, Any] = {}
    host_executor = None
    if execution_boundary == "host_process":
        from scripts.devfarm_host_dispatch import create_host_process_executor

        host_executor = create_host_process_executor(
            root_path / ".devfarm" / "host-dispatch",
            timeout_seconds=timeout_seconds,
        )
    for task in plan["tasks"]:
        if task["owner"] != "worker" or task["status"] not in {"READY", "PLANNED"}:
            continue
        assignment = task["assignment"]
        selected_provider = provider_id or assignment.get("provider_id")
        selected_model = model_id or assignment.get("model_id")
        if not selected_provider or not selected_model:
            raise DevFarmError(f"provider and model are required for worker task: {task['task_id']}")
        provider = build_cli_provider(selected_provider, selected_model, timeout_seconds)
        providers[task["task_id"]] = provider
        if host_executor is not None:
            from src.dev_agent.providers.host_dispatch import HostProviderDispatch

            host_dispatches[task["task_id"]] = HostProviderDispatch(
                provider,
                execution_boundary="host_process",
                executor=host_executor,
            )
    return dispatch_plan(
        root_path,
        run_id,
        providers=providers,
        host_dispatches=host_dispatches or None,
    )


__all__ = [
    "CommanderPlanStore",
    "PLAN_SCHEMA_VERSION",
    "PlanConflictError",
    "collect_plan",
    "build_cli_provider",
    "create_plan",
    "dispatch_cli",
    "dispatch_plan",
    "load_worker_manifest",
    "list_active_ownership",
    "mark_integrated",
    "recover_orphaned_dispatches",
    "reassign_task",
    "record_result",
    "refresh_plan",
    "resume_plan",
    "supersede_plan",
    "summarize_delegation",
    "validate_plan",
    "verified_worker_patch",
    "verify_plan",
]
