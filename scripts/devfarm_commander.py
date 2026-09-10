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
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from typing import Any, Mapping, Sequence
import uuid

from scripts.devfarm import DevFarmError, init_farm, validate_manifest, validate_patch, validate_result
from scripts.devfarm_orchestrator import DevFarmOrchestrator, WorkerAssignment
from src.dev_agent.providers.base import ModelProvider
from src.dev_agent.security.protected_paths import PROTECTED_AUTHORITY_PATHS, is_protected_path


PLAN_SCHEMA_VERSION = 1
_PLAN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
_PLAN_STATUSES = frozenset(
    {
        "PLANNED",
        "READY",
        "DISPATCHED",
        "PROPOSED",
        "HOST_VERIFIED",
        "REJECTED",
        "BLOCKED",
        "INTEGRATED",
        "SUPERSEDED",
    }
)
_OWNERS = frozenset({"codex", "worker"})
_DEPENDENCY_COMPLETE = frozenset({"INTEGRATED"})
_DEPENDENCY_FAILURE = frozenset({"REJECTED", "BLOCKED", "SUPERSEDED"})
_PROTECTED_PATHS = PROTECTED_AUTHORITY_PATHS


class PlanConflictError(DevFarmError):
    """A Commander plan changed after a caller loaded its revision."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, name: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DevFarmError(f"{name} must be a non-empty string")
    result = value.strip()
    if len(result) > max_length:
        raise DevFarmError(f"{name} is too long")
    return result


def _plan_id(value: Any) -> str:
    result = _text(value, "run_id", max_length=101)
    if not _PLAN_ID.fullmatch(result):
        raise DevFarmError("run_id contains unsafe characters")
    return result


def _path(value: Any, name: str) -> str:
    result = _text(value, name, max_length=400).replace("\\", "/")
    parsed = PurePosixPath(result)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise DevFarmError(f"{name} must be a safe relative path")
    return str(parsed)


def _paths(value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise DevFarmError(f"{name} must be a list")
    result: list[str] = []
    for item in value:
        normalized = _path(item, name)
        if normalized not in result:
            result.append(normalized)
    return result


def _revision(value: Any) -> str:
    result = _text(value, "base_revision", max_length=200)
    if result.startswith("-") or any(char.isspace() or char in "\r\n" for char in result):
        raise DevFarmError("base_revision must be a safe Git revision")
    return result


def _is_protected(path: str) -> bool:
    return is_protected_path(path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DevFarmError(f"could not read JSON file {path}") from exc


def _repository_path(root: Path, relative: str, *, required_parent: str | None = None) -> Path:
    candidate = (root / relative).resolve()
    root = root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise DevFarmError("plan path resolves outside repository") from exc
    if required_parent is not None:
        parent = (root / required_parent).resolve()
        try:
            candidate.relative_to(parent)
        except ValueError as exc:
            raise DevFarmError(f"plan path must stay under {required_parent}") from exc
    return candidate


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root.as_posix()}", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise DevFarmError(result.stderr.strip() or "Git command failed")
    return result.stdout.strip()


def _resolved_revision(root: Path, revision: str) -> str:
    try:
        return _git(root, "rev-parse", "--verify", f"{revision}^{{commit}}")
    except DevFarmError as exc:
        raise DevFarmError(f"plan base_revision cannot be resolved: {revision}") from exc


def _require_current_revision(root: Path, revision: str) -> str:
    expected = _resolved_revision(root, revision)
    actual = _git(root, "rev-parse", "HEAD")
    if actual != expected:
        raise DevFarmError(f"repository HEAD does not match plan base_revision: {actual} != {expected}")
    return expected


def _dependency_records(value: Any, tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if value is None:
        return [
            {"task_id": task["task_id"], "depends_on": list(task["dependencies"])}
            for task in tasks
        ]
    if not isinstance(value, list):
        raise DevFarmError("dependencies must be a list")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise DevFarmError("dependency records must be objects")
        task_id = _text(item.get("task_id"), "dependency task_id", max_length=101)
        depends_on = item.get("depends_on", item.get("dependencies", []))
        if not isinstance(depends_on, list):
            raise DevFarmError("dependency depends_on must be a list")
        normalized = []
        for dependency in depends_on:
            dependency_id = _text(dependency, "dependency id", max_length=101)
            if dependency_id not in normalized:
                normalized.append(dependency_id)
        records.append({"task_id": task_id, "depends_on": normalized})
    return records


def _ownership_records(value: Any, tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if value is None:
        return [{"task_id": task["task_id"], "paths": list(task["ownership"])} for task in tasks]
    if not isinstance(value, list):
        raise DevFarmError("ownership must be a list")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise DevFarmError("ownership records must be objects")
        records.append(
            {
                "task_id": _text(item.get("task_id"), "ownership task_id", max_length=101),
                "paths": _paths(item.get("paths", []), "ownership paths"),
            }
        )
    return records


def _assignment_record(item: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    owner = _text(item.get("owner", task["owner"]), "assignment owner", max_length=16).lower()
    if owner not in _OWNERS:
        raise DevFarmError("assignment owner must be codex or worker")
    if owner != task["owner"]:
        raise DevFarmError(f"assignment owner does not match task owner: {task['task_id']}")
    record: dict[str, Any] = {"task_id": task["task_id"], "owner": owner}
    for key in ("provider_id", "provider_binding_id", "model_id"):
        value = item.get(key)
        if value is not None:
            record[key] = _text(value, f"assignment {key}")
    if owner == "worker":
        if not record.get("provider_id") or not record.get("model_id"):
            raise DevFarmError(f"worker assignment requires provider_id and model_id: {task['task_id']}")
    return record


def _assignment_records(value: Any, tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_task: dict[str, Mapping[str, Any]] = {}
    for task in tasks:
        nested = task.get("assignment")
        if nested is None:
            nested = {}
        if not isinstance(nested, Mapping):
            raise DevFarmError("task assignment must be an object")
        by_task[task["task_id"]] = nested
    if value is not None:
        if not isinstance(value, list):
            raise DevFarmError("assignments must be a list")
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, Mapping):
                raise DevFarmError("assignment records must be objects")
            task_id = _text(item.get("task_id"), "assignment task_id", max_length=101)
            if task_id not in by_task:
                raise DevFarmError(f"assignment references unknown task: {task_id}")
            if task_id in seen:
                raise DevFarmError(f"assignments must not contain duplicate task ids: {task_id}")
            seen.add(task_id)
            by_task[task_id] = item
    records: list[dict[str, Any]] = []
    for task in tasks:
        records.append(_assignment_record(by_task[task["task_id"]], task))
    return records


def _check_unique_ids(values: Sequence[str], name: str) -> None:
    if len(values) != len(set(values)):
        raise DevFarmError(f"{name} must not contain duplicate task ids")


def _check_ownership(records: Sequence[Mapping[str, Any]]) -> None:
    seen: list[tuple[str, str]] = []
    for record in records:
        task_id = str(record["task_id"])
        paths = record.get("paths", [])
        for path in paths:
            if _is_protected(path):
                raise DevFarmError(f"protected path cannot be owned by a plan task: {path}")
            current = PurePosixPath(path)
            for previous_task, previous_path in seen:
                previous = PurePosixPath(previous_path)
                if current == previous or current in previous.parents or previous in current.parents:
                    raise DevFarmError(
                        f"ownership paths overlap between {previous_task} and {task_id}: {previous_path}, {path}"
                    )
            seen.append((task_id, path))


def _check_dependency_cycles(tasks: Sequence[Mapping[str, Any]]) -> None:
    graph = {task["task_id"]: tuple(task["dependencies"]) for task in tasks}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise DevFarmError(f"dependency cycle detected at task: {task_id}")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in graph[task_id]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in graph:
        visit(task_id)


def validate_plan(value: Mapping[str, Any], *, root: str | Path | None = None) -> dict[str, Any]:
    """Normalize and validate a Commander parent plan.

    The normalized shape deliberately repeats dependency, ownership, and
    assignment indexes at the top level so a status reader can reconstruct the
    plan without walking every task.  Nested values remain the task-local
    source used by dispatch.
    """

    if not isinstance(value, Mapping):
        raise DevFarmError("plan must be an object")
    if value.get("schema_version", PLAN_SCHEMA_VERSION) != PLAN_SCHEMA_VERSION:
        raise DevFarmError("unsupported Commander plan schema version")
    run_id = _plan_id(value.get("run_id"))
    objective = _text(value.get("objective"), "objective", max_length=4000)
    base_revision = _revision(value.get("base_revision"))
    raw_tasks = value.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise DevFarmError("plan tasks must be a non-empty list")
    root_path = Path(root).resolve() if root is not None else None
    tasks: list[dict[str, Any]] = []
    task_ids: list[str] = []
    for raw in raw_tasks:
        if not isinstance(raw, Mapping):
            raise DevFarmError("plan tasks must be objects")
        task_id = _text(raw.get("task_id"), "task_id", max_length=101)
        if not _PLAN_ID.fullmatch(task_id):
            raise DevFarmError("task_id contains unsafe characters")
        task_ids.append(task_id)
        owner = _text(raw.get("owner"), "owner", max_length=16).lower()
        if owner not in _OWNERS:
            raise DevFarmError("owner must be codex or worker")
        status = _text(raw.get("status", "PLANNED"), "task status", max_length=32).upper()
        if status not in _PLAN_STATUSES:
            raise DevFarmError(f"unsupported task status: {status}")
        dependencies = raw.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise DevFarmError("task dependencies must be a list")
        normalized_dependencies = []
        for dependency in dependencies:
            dependency_id = _text(dependency, "dependency id", max_length=101)
            if dependency_id not in normalized_dependencies:
                normalized_dependencies.append(dependency_id)
        if task_id in normalized_dependencies:
            raise DevFarmError("a task cannot depend on itself")
        ownership = _paths(raw.get("ownership", []), "task ownership")
        manifest_path = raw.get("manifest_path")
        normalized_manifest_path = None if manifest_path is None else _path(manifest_path, "manifest_path")
        if owner == "worker":
            if normalized_manifest_path is None:
                raise DevFarmError(f"worker task requires manifest_path: {task_id}")
            if root_path is not None:
                manifest_file = _repository_path(root_path, normalized_manifest_path, required_parent=".devfarm/tasks")
                manifest = validate_manifest(_read_json(manifest_file))
                if manifest["task_id"] != task_id:
                    raise DevFarmError(f"manifest task_id does not match plan task: {task_id}")
                if not ownership:
                    ownership = list(manifest["allowed_files"])
                if not set(manifest["allowed_files"]).issubset(set(ownership)):
                    raise DevFarmError(f"manifest allowed_files exceed declared ownership: {task_id}")
        elif normalized_manifest_path is not None:
            raise DevFarmError(f"Codex task cannot have a worker manifest: {task_id}")
        max_attempts = raw.get("max_attempts", 1)
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or max_attempts <= 0:
            raise DevFarmError("max_attempts must be a positive integer")
        attempt_count = raw.get("attempt_count", 0)
        if isinstance(attempt_count, bool) or not isinstance(attempt_count, int) or attempt_count < 0 or attempt_count > max_attempts:
            raise DevFarmError("attempt_count must be between zero and max_attempts")
        task: dict[str, Any] = {
            "task_id": task_id,
            "owner": owner,
            "status": status,
            "dependencies": normalized_dependencies,
            "ownership": ownership,
            "manifest_path": normalized_manifest_path,
            "max_attempts": max_attempts,
            "attempt_count": attempt_count,
        }
        worker_candidate = raw.get("worker_candidate", owner == "worker")
        if not isinstance(worker_candidate, bool):
            raise DevFarmError("worker_candidate must be a boolean")
        task["worker_candidate"] = worker_candidate
        task["delegation_reason"] = _text(
            raw.get(
                "delegation_reason",
                "worker_assignment" if owner == "worker" else "legacy_plan_reason_not_recorded",
            ),
            "delegation_reason",
            max_length=1000,
        )
        assignment = raw.get("assignment", {})
        if assignment is not None:
            if not isinstance(assignment, Mapping):
                raise DevFarmError("task assignment must be an object")
            task["assignment"] = dict(assignment)
        if raw.get("result_ref") is not None:
            task["result_ref"] = _path(raw["result_ref"], "result_ref")
        if raw.get("last_attempt_id") is not None:
            task["last_attempt_id"] = _text(raw["last_attempt_id"], "last_attempt_id", max_length=101)
        if raw.get("block_reason") is not None:
            task["block_reason"] = _text(raw["block_reason"], "block_reason", max_length=1000)
        if raw.get("last_result_status") is not None:
            task["last_result_status"] = _text(raw["last_result_status"], "last_result_status", max_length=32)
        if raw.get("last_error") is not None:
            task["last_error"] = _text(raw["last_error"], "last_error", max_length=1000)
        if raw.get("integration_note") is not None:
            task["integration_note"] = _text(raw["integration_note"], "integration_note", max_length=2000)
        if raw.get("manifest_history") is not None:
            task["manifest_history"] = _paths(raw["manifest_history"], "manifest_history")
        for key in ("target_ref", "integration_revision", "source_attempt_id"):
            if raw.get(key) is not None:
                task[key] = _text(raw[key], key, max_length=200)
        if raw.get("verified_patch_digest") is not None:
            digest = _text(raw["verified_patch_digest"], "verified_patch_digest", max_length=64).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise DevFarmError("verified_patch_digest must be a SHA-256 hex digest")
            task["verified_patch_digest"] = digest
        tasks.append(task)
    _check_unique_ids(task_ids, "plan tasks")
    task_id_set = set(task_ids)

    dependencies = _dependency_records(value.get("dependencies"), tasks)
    dependency_task_ids = [str(record["task_id"]) for record in dependencies]
    _check_unique_ids(dependency_task_ids, "dependencies")
    dependency_by_task = {record["task_id"]: record["depends_on"] for record in dependencies}
    if set(dependency_by_task) != task_id_set:
        raise DevFarmError("dependencies must contain exactly one record for every task")
    for task in tasks:
        normalized = list(dependency_by_task[task["task_id"]])
        if any(dependency not in task_id_set for dependency in normalized):
            raise DevFarmError(f"dependency references an unknown task: {task['task_id']}")
        task["dependencies"] = normalized
    dependencies = [{"task_id": task["task_id"], "depends_on": list(task["dependencies"])} for task in tasks]
    _check_dependency_cycles(tasks)

    ownership = _ownership_records(value.get("ownership"), tasks)
    ownership_task_ids = [str(record["task_id"]) for record in ownership]
    _check_unique_ids(ownership_task_ids, "ownership")
    if set(ownership_task_ids) != task_id_set:
        raise DevFarmError("ownership must contain exactly one record for every task")
    ownership_by_task = {record["task_id"]: list(record["paths"]) for record in ownership}
    for task in tasks:
        if ownership_by_task[task["task_id"]]:
            task["ownership"] = ownership_by_task[task["task_id"]]
        elif task["ownership"]:
            ownership_by_task[task["task_id"]] = list(task["ownership"])
        task["ownership"] = list(ownership_by_task[task["task_id"]])
    ownership = [{"task_id": task["task_id"], "paths": list(task["ownership"])} for task in tasks]
    _check_ownership(ownership)

    assignments = _assignment_records(value.get("assignments"), tasks)
    assignments_by_task = {record["task_id"]: record for record in assignments}
    for task in tasks:
        task["assignment"] = dict(assignments_by_task[task["task_id"]])

    results = value.get("results", [])
    if not isinstance(results, list):
        raise DevFarmError("results must be a list")
    normalized_results: list[dict[str, Any]] = []
    for raw_result in results:
        if not isinstance(raw_result, Mapping):
            raise DevFarmError("plan result records must be objects")
        result_record = {
            "task_id": _text(raw_result.get("task_id"), "result task_id", max_length=101),
            "stage": _text(raw_result.get("stage"), "result stage", max_length=64),
            "status": _text(raw_result.get("status"), "result status", max_length=32),
        }
        if result_record["task_id"] not in task_id_set:
            raise DevFarmError(f"result references unknown task: {result_record['task_id']}")
        if raw_result.get("result_ref") is not None:
            result_record["result_ref"] = _path(raw_result["result_ref"], "result_ref")
        if raw_result.get("attempt_id") is not None:
            result_record["attempt_id"] = _text(raw_result["attempt_id"], "result attempt_id", max_length=101)
        if raw_result.get("recorded_at") is not None:
            result_record["recorded_at"] = _text(raw_result["recorded_at"], "recorded_at", max_length=80)
        normalized_results.append(result_record)

    plan_status = _text(value.get("status", "PLANNED"), "plan status", max_length=32).upper()
    if plan_status not in _PLAN_STATUSES:
        raise DevFarmError(f"unsupported plan status: {plan_status}")
    plan_revision = value.get("plan_revision", 0)
    if isinstance(plan_revision, bool) or not isinstance(plan_revision, int) or plan_revision < 0:
        raise DevFarmError("plan_revision must be a non-negative integer")
    normalized = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "run_id": run_id,
        "objective": objective,
        "base_revision": base_revision,
        "status": plan_status,
        "tasks": tasks,
        "dependencies": dependencies,
        "ownership": ownership,
        "assignments": assignments,
        "results": normalized_results,
        "plan_revision": plan_revision,
        "created_at": _text(value.get("created_at", _now()), "created_at", max_length=80),
        "updated_at": _text(value.get("updated_at", _now()), "updated_at", max_length=80),
    }
    return normalized


def refresh_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    """Release dependency-ready tasks without dispatching or mutating code."""

    plan = deepcopy(validate_plan(value))
    by_id = {task["task_id"]: task for task in plan["tasks"]}
    changed = True
    while changed:
        changed = False
        for task in plan["tasks"]:
            if task["status"] != "PLANNED":
                continue
            dependency_statuses = [by_id[item]["status"] for item in task["dependencies"]]
            if any(status in _DEPENDENCY_FAILURE for status in dependency_statuses):
                task["status"] = "BLOCKED"
                task["block_reason"] = "dependency_failed"
                changed = True
            elif all(status in _DEPENDENCY_COMPLETE for status in dependency_statuses):
                task["status"] = "READY"
                changed = True
    statuses = [task["status"] for task in plan["tasks"]]
    if statuses and all(status == "INTEGRATED" for status in statuses):
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
        with self._lock(path):
            if path.exists():
                raise FileExistsError(path)
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


def _manifest_for(root: Path, task: Mapping[str, Any]) -> tuple[Path, dict[str, Any]]:
    relative = task.get("manifest_path")
    if not isinstance(relative, str):
        raise DevFarmError(f"worker task has no manifest path: {task['task_id']}")
    path = _repository_path(root, relative, required_parent=".devfarm/tasks")
    manifest = validate_manifest(_read_json(path))
    if manifest["task_id"] != task["task_id"]:
        raise DevFarmError(f"manifest task_id does not match plan task: {task['task_id']}")
    if not set(manifest["allowed_files"]).issubset(set(task["ownership"])):
        raise DevFarmError(f"manifest allowed_files exceed plan ownership: {task['task_id']}")
    return path, manifest


def _result_ref(task_id: str, attempt_id: str | None = None) -> str:
    if attempt_id is None:
        return f".devfarm/results/{task_id}/result.json"
    return f".devfarm/results/{task_id}/attempts/{_text(attempt_id, 'attempt_id', max_length=101)}/result.json"


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


def _apply_proposal_result(plan: dict[str, Any], task: dict[str, Any], result: Mapping[str, Any]) -> None:
    status = str(result.get("status", "failed"))
    attempt_id = result.get("attempt_id")
    result_ref = _result_ref(task["task_id"], attempt_id if isinstance(attempt_id, str) and attempt_id.strip() else None)
    task["result_ref"] = result_ref
    if attempt_id is not None:
        task["last_attempt_id"] = _text(attempt_id, "attempt_id", max_length=101)
    task["last_result_status"] = status
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


def dispatch_plan(
    root: str | Path,
    run_id: str,
    *,
    providers: Mapping[str, ModelProvider],
    orchestrator: DevFarmOrchestrator | None = None,
) -> dict[str, Any]:
    """Dispatch all currently READY worker tasks through existing proposals."""

    root_path = Path(root).resolve()
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
    plan = store.save(plan)
    if not ready:
        return refresh_plan(plan)
    farm = orchestrator or DevFarmOrchestrator()
    try:
        proposals = farm.propose(root_path, [(manifest_path, provider) for _task_item, manifest_path, provider in ready])
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
        _manifest_path, manifest = _manifest_for(root_path, task)
        result_path = _repository_path(root_path, _result_ref(task["task_id"]), required_parent=".devfarm/results")
        if not result_path.is_file():
            continue
        result = validate_result(_read_json(result_path), manifest=manifest)
        attempt_id = result.get("attempt_id")
        attempt_ref = _result_ref(task["task_id"], attempt_id if isinstance(attempt_id, str) and attempt_id.strip() else None)
        task["result_ref"] = attempt_ref
        if attempt_id is not None:
            task["last_attempt_id"] = _text(attempt_id, "attempt_id", max_length=101)
        task["last_result_status"] = result["status"]
        metrics = result.get("worker_metrics", {})
        host_verified = isinstance(metrics, Mapping) and metrics.get("host_verified") is True
        accepted = isinstance(metrics, Mapping) and metrics.get("result_accepted") is True
        if task["status"] != "INTEGRATED":
            if result["status"] == "completed" and host_verified and accepted:
                task["status"] = "HOST_VERIFIED"
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
) -> dict[str, Any]:
    """Host-verify PROPOSED tasks through the existing bounded verifier."""

    root_path = Path(root).resolve()
    store = CommanderPlanStore(root_path)
    plan = collect_plan(root_path, run_id)
    wanted = set(task_ids) if task_ids is not None else None
    selected = [
        task
        for task in plan["tasks"]
        if task["owner"] == "worker" and task["status"] == "PROPOSED" and (wanted is None or task["task_id"] in wanted)
    ]
    if wanted is not None and any(item not in {task["task_id"] for task in selected} for item in wanted):
        missing = sorted(wanted - {task["task_id"] for task in selected})
        raise DevFarmError(f"tasks are not ready for host verification: {', '.join(missing)}")
    if not selected:
        return plan
    farm = orchestrator or DevFarmOrchestrator()
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
        current["status"] = "HOST_VERIFIED" if accepted else "REJECTED"
        current["last_result_status"] = result.get("status")
        attempt_id = result.get("attempt_id")
        if attempt_id is not None:
            current["last_attempt_id"] = _text(attempt_id, "attempt_id", max_length=101)
        if not accepted:
            current["block_reason"] = "host_verification_failed"
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


def reassign_task(
    root: str | Path,
    run_id: str,
    task_id: str,
    *,
    provider_id: str,
    model_id: str,
    provider_binding_id: str | None = None,
) -> dict[str, Any]:
    store = CommanderPlanStore(root)
    plan = store.load(run_id)
    task = _task(plan, task_id)
    if task["owner"] != "worker":
        raise DevFarmError("only worker tasks can be reassigned")
    if task["status"] not in {"REJECTED", "BLOCKED"}:
        raise DevFarmError("only rejected or blocked worker tasks can be reassigned")
    if task["attempt_count"] >= task["max_attempts"]:
        raise DevFarmError("worker task attempt limit reached")
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
    task.pop("block_reason", None)
    task.pop("last_error", None)
    return store.save(refresh_plan(plan))


def _git_diff_digest(root: Path, revision: str) -> str:
    result = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root.as_posix()}",
            "diff-tree",
            "--root",
            "--binary",
            "--no-commit-id",
            "-r",
            revision,
            "--",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise DevFarmError(result.stderr.strip() or "could not read integration revision diff")
    return hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()


def _verified_worker_patch(root: Path, task: Mapping[str, Any]) -> tuple[str, dict[str, Any], str]:
    if task.get("owner") != "worker":
        raise DevFarmError("verified worker patch is required only for worker tasks")
    manifest_path, manifest = _manifest_for(root, task)
    attempt_id = task.get("last_attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise DevFarmError("worker integration requires a verified attempt_id")
    result_ref = task.get("result_ref") or _result_ref(task["task_id"], attempt_id)
    result_path = _repository_path(root, result_ref, required_parent=".devfarm/results")
    if not result_path.is_file():
        raise DevFarmError("worker integration result artifact is missing")
    result = validate_result(_read_json(result_path), manifest=manifest)
    if result.get("attempt_id") != attempt_id:
        raise DevFarmError("integration source_attempt_id does not match result artifact")
    metrics = result.get("worker_metrics")
    if result.get("status") != "completed" or not isinstance(metrics, Mapping) or metrics.get("host_verified") is not True or metrics.get("result_accepted") is not True:
        raise DevFarmError("worker integration requires an accepted Host Verification result")
    patch_path = result_path.parent / "patch.diff"
    if not patch_path.is_file():
        raise DevFarmError("verified worker patch artifact is missing")
    try:
        patch = patch_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DevFarmError("verified worker patch artifact cannot be read") from exc
    actual_changed_files = validate_patch(patch, manifest=manifest)
    if not actual_changed_files:
        raise DevFarmError("worker integration requires a non-empty verified patch")
    return patch, manifest, attempt_id


def _prove_worker_patch_in_revision(root: Path, patch: str, revision: str, changed_files: Sequence[str]) -> None:
    """Compare the verified patch result with the target commit tree.

    A temporary detached worktree keeps this proof independent of the
    caller's checkout.  Only the files actually changed by the verified
    patch are compared, so one integration commit may contain several
    independently reviewed Worker patches.
    """

    try:
        parent = _git(root, "rev-parse", f"{revision}^{{commit}}^")
    except DevFarmError as exc:
        raise DevFarmError("integration revision must have a parent commit") from exc
    with tempfile.TemporaryDirectory(prefix="devfarm-integration-") as directory:
        worktree = Path(directory)
        _git(root, "worktree", "add", "--detach", worktree.as_posix(), parent)
        try:
            applied = subprocess.run(
                ["git", "-c", f"safe.directory={worktree.as_posix()}", "apply", "--whitespace=error", "-"],
                cwd=worktree,
                input=patch,
                capture_output=True,
                text=True,
                check=False,
            )
            if applied.returncode != 0:
                detail = applied.stderr.strip() or applied.stdout.strip() or "patch does not apply to integration parent"
                raise DevFarmError(detail)
            _git(worktree, "add", "--all")
            compared = subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={worktree.as_posix()}",
                    "diff",
                    "--cached",
                    "--exit-code",
                    revision,
                    "--",
                    *changed_files,
                ],
                cwd=worktree,
                capture_output=True,
                text=True,
                check=False,
            )
            if compared.returncode != 0:
                raise DevFarmError("verified worker patch is not reflected in integration revision")
        finally:
            subprocess.run(
                ["git", "-c", f"safe.directory={root.as_posix()}", "worktree", "remove", "--force", worktree.as_posix()],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )


def _advance_dependent_manifest_baselines(root: Path, plan: dict[str, Any], integrated_task_id: str, baseline_revision: str) -> None:
    """Issue a new manifest when a code dependency is actually integrated.

    Existing manifests are immutable records of the proposal input.  A
    dependent task therefore receives a new manifest file instead of having
    its old baseline rewritten in place.  The caller only invokes this after
    Git-backed integration evidence has been proven.
    """

    for dependent in plan["tasks"]:
        if integrated_task_id not in dependent.get("dependencies", []):
            continue
        if dependent.get("owner") != "worker" or dependent.get("status") not in {"PLANNED", "READY"}:
            continue
        if any(_task(plan, dependency).get("status") != "INTEGRATED" for dependency in dependent.get("dependencies", [])):
            continue
        for dependency in dependent.get("dependencies", []):
            dependency_revision = _task(plan, dependency).get("integration_revision")
            if not isinstance(dependency_revision, str) or not dependency_revision.strip():
                raise DevFarmError("integrated code dependency has no integration revision")
            try:
                _git(root, "merge-base", "--is-ancestor", dependency_revision, baseline_revision)
            except DevFarmError as exc:
                raise DevFarmError("dependent task baseline does not contain every integrated dependency") from exc
        old_relative = dependent.get("manifest_path")
        if not isinstance(old_relative, str):
            raise DevFarmError(f"dependent worker task has no manifest path: {dependent['task_id']}")
        old_path = _repository_path(root, old_relative, required_parent=".devfarm/tasks")
        old_manifest = validate_manifest(_read_json(old_path))
        if old_manifest["base_revision"] == baseline_revision:
            continue
        new_name = f"{old_path.stem}.base-{baseline_revision[:12]}{old_path.suffix}"
        new_path = old_path.with_name(new_name)
        new_manifest = dict(_read_json(old_path))
        new_manifest["base_revision"] = baseline_revision
        normalized = validate_manifest(new_manifest)
        if new_path.exists():
            if validate_manifest(_read_json(new_path))["base_revision"] != baseline_revision:
                raise DevFarmError(f"dependent manifest baseline path already exists with another revision: {new_name}")
        else:
            temporary = new_path.with_name(f".{new_path.name}.{uuid.uuid4().hex}.tmp")
            temporary.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, new_path)
        new_relative = new_path.relative_to(root).as_posix()
        history = list(dependent.get("manifest_history", []))
        if old_relative not in history:
            history.append(old_relative)
        if new_relative not in history:
            history.append(new_relative)
        dependent["manifest_history"] = history
        dependent["manifest_path"] = new_relative


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
    root_path = Path(root).resolve()
    store = CommanderPlanStore(root_path)
    plan = store.load(run_id)
    task = _task(plan, task_id)
    note = _text(note, "integration note", max_length=2000)
    target_ref = _text(target_ref, "target_ref", max_length=200)
    integration_revision = _text(integration_revision, "integration_revision", max_length=200)
    source_attempt_id = _text(source_attempt_id, "source_attempt_id", max_length=200)
    verified_patch_digest = _text(verified_patch_digest, "verified_patch_digest", max_length=64).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", verified_patch_digest):
        raise DevFarmError("verified_patch_digest must be a SHA-256 hex digest")
    target_commit = _resolved_revision(root_path, target_ref)
    integration_commit = _resolved_revision(root_path, integration_revision)
    try:
        _git(root_path, "merge-base", "--is-ancestor", integration_commit, target_commit)
    except DevFarmError as exc:
        raise DevFarmError("integration revision is not contained in target_ref") from exc
    if task["owner"] == "worker":
        if task["status"] != "HOST_VERIFIED":
            raise DevFarmError("worker task requires host verification before integration")
        patch, manifest, expected_attempt_id = _verified_worker_patch(root_path, task)
        if source_attempt_id != expected_attempt_id:
            raise DevFarmError("source_attempt_id does not match the latest verified attempt")
        digest = hashlib.sha256(patch.encode("utf-8")).hexdigest()
        if verified_patch_digest != digest:
            raise DevFarmError("verified patch digest does not match the Host Verification artifact")
        changed_files = validate_patch(patch, manifest=manifest)
        _prove_worker_patch_in_revision(root_path, patch, integration_commit, changed_files)
    elif task["owner"] == "codex":
        if task["status"] != "READY":
            raise DevFarmError("Codex task must be READY before integration marking")
        if _git_diff_digest(root_path, integration_commit) != verified_patch_digest:
            raise DevFarmError("Codex integration digest does not match the integration revision")
    else:
        raise DevFarmError("unsupported plan task owner")
    task["status"] = "INTEGRATED"
    task["integration_note"] = note
    task["target_ref"] = target_ref
    task["integration_revision"] = integration_commit
    task["source_attempt_id"] = source_attempt_id
    task["verified_patch_digest"] = verified_patch_digest
    _advance_dependent_manifest_baselines(root_path, plan, task_id, target_commit)
    _record_result(plan, task_id, "integration", "integrated", task.get("result_ref"), attempt_id=source_attempt_id)
    return store.save(refresh_plan(plan))


def _cli_provider(provider_id: str, model_id: str, timeout_seconds: float) -> ModelProvider:
    # ProviderFactory and the existing DevFarm activation policy remain the
    # only construction/activation boundary.  This import is intentionally
    # local so importing the plan store never constructs a provider.
    from scripts.devfarm_worker import _provider

    return _provider(provider_id, model_id, timeout_seconds)


def dispatch_cli(
    root: str | Path,
    run_id: str,
    *,
    provider_id: str | None = None,
    model_id: str | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    plan = CommanderPlanStore(root).load(run_id)
    providers: dict[str, ModelProvider] = {}
    for task in plan["tasks"]:
        if task["owner"] != "worker" or task["status"] not in {"READY", "PLANNED"}:
            continue
        assignment = task["assignment"]
        selected_provider = provider_id or assignment.get("provider_id")
        selected_model = model_id or assignment.get("model_id")
        if not selected_provider or not selected_model:
            raise DevFarmError(f"provider and model are required for worker task: {task['task_id']}")
        providers[task["task_id"]] = _cli_provider(selected_provider, selected_model, timeout_seconds)
    return dispatch_plan(root, run_id, providers=providers)


__all__ = [
    "CommanderPlanStore",
    "PLAN_SCHEMA_VERSION",
    "PlanConflictError",
    "collect_plan",
    "create_plan",
    "dispatch_cli",
    "dispatch_plan",
    "mark_integrated",
    "reassign_task",
    "refresh_plan",
    "resume_plan",
    "validate_plan",
    "verify_plan",
]
