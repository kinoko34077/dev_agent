"""Durable development-plan state and persistence boundary.

This module owns the JSON plan store, optimistic revision checks, dependency
readiness projection, and bounded result records used by Commander,
Supervisor, and Host integration.  It does not dispatch providers, verify
patches, or mutate the official checkout.
"""

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping
import uuid

from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_workspace import init_farm
from scripts import devfarm_plan_validation as plan_validation
from scripts.devfarm_plan_ownership import load_ownership_projections, plan_paths
from scripts.devfarm_repository import read_json


_DEPENDENCY_COMPLETE = plan_validation.DEPENDENCY_COMPLETE
_DEPENDENCY_FAILURE = plan_validation.DEPENDENCY_FAILURE
_ACTIVE_TASK_STATUSES = plan_validation.ACTIVE_TASK_STATUSES
_read_json = read_json
_text = plan_validation.text
_plan_id = plan_validation.plan_id
_ownership_conflict = plan_validation.ownership_conflict
validate_plan = plan_validation.validate_plan


class PlanConflictError(DevFarmError):
    """A development plan changed after a caller loaded its revision."""


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
        """Hold a short-lived OS lock while comparing and replacing a plan."""

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
        return [self.load(path.stem) for path in plan_paths(self.directory)]

    def _plans_for_ownership(self) -> list[dict[str, Any]]:
        return load_ownership_projections(self.root, self.directory)

    def active_ownership_conflicts(self, plan: Mapping[str, Any]) -> list[dict[str, str]]:
        """Return path conflicts with unfinished plans before a new plan is saved."""

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
        """Return the durable file ownership projection for active plan work."""

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


def record_result(
    plan: dict[str, Any],
    task_id: str,
    stage: str,
    status: str,
    result_ref: str | None = None,
    *,
    attempt_id: str | None = None,
) -> None:
    """Record one bounded task-stage result in a mutable plan projection."""

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


__all__ = [
    "CommanderPlanStore",
    "PlanConflictError",
    "record_result",
    "refresh_plan",
    "validate_plan",
]
