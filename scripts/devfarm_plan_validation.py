"""Host-owned validation and normalization for development Commander plans.

This module deliberately owns plan-shape, dependency, ownership, assignment,
and delegation validation only.  It does not persist plans, dispatch Workers,
or integrate Git changes; those responsibilities remain in Commander and the
existing Host services.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Sequence

from scripts.devfarm import DevFarmError, validate_manifest
from scripts.devfarm_repository import git, read_json, repository_path, resolved_revision
from scripts.devfarm_supervisor_protocol import normalize_review_decision, normalize_supervisor_metadata
from src.dev_agent.coordination import WorkAddress, allocate_work_address
from src.dev_agent.security.protected_paths import is_protected_path


PLAN_SCHEMA_VERSION = 1
PLAN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
PLAN_STATUSES = frozenset(
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
OWNERS = frozenset({"codex", "worker"})
TASK_RISKS = frozenset({"low", "normal", "high", "critical"})
TASK_SENSITIVITIES = frozenset({"public", "normal", "internal", "sensitive"})
DEPENDENCY_COMPLETE = frozenset({"INTEGRATED"})
DEPENDENCY_FAILURE = frozenset({"REJECTED", "BLOCKED", "SUPERSEDED"})
SUPPORTED_DEPENDENCY_TYPES = frozenset({"CODE_INTEGRATED"})
ACTIVE_TASK_STATUSES = frozenset(
    {"PLANNED", "READY", "DISPATCHED", "PROPOSED", "HOST_VERIFIED", "REJECTED", "BLOCKED"}
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def text(value: Any, name: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DevFarmError(f"{name} must be a non-empty string")
    result = value.strip()
    if len(result) > max_length:
        raise DevFarmError(f"{name} is too long")
    return result


def plan_id(value: Any) -> str:
    result = text(value, "run_id", max_length=101)
    if not PLAN_ID_PATTERN.fullmatch(result):
        raise DevFarmError("run_id contains unsafe characters")
    return result


def path(value: Any, name: str) -> str:
    result = text(value, name, max_length=400).replace("\\", "/")
    parsed = PurePosixPath(result)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise DevFarmError(f"{name} must be a safe relative path")
    return str(parsed)


def paths(value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise DevFarmError(f"{name} must be a list")
    result: list[str] = []
    for item in value:
        normalized = path(item, name)
        if normalized not in result:
            result.append(normalized)
    return result


def revision(value: Any) -> str:
    result = text(value, "base_revision", max_length=200)
    if result.startswith("-") or any(char.isspace() or char in "\r\n" for char in result):
        raise DevFarmError("base_revision must be a safe Git revision")
    return result


def work_address(value: Any, name: str = "work_address") -> str:
    """Validate the optional human-readable address without replacing UUIDs."""

    try:
        return str(WorkAddress.parse(value))
    except (TypeError, ValueError) as exc:
        raise DevFarmError(f"{name} is invalid") from exc


def work_address_kind(value: Any, *, default: str = "numeric") -> str:
    """Validate Host-owned address allocation intent."""

    if value is None:
        value = default
    if not isinstance(value, str) or value.strip().lower() not in {"numeric", "letter"}:
        raise DevFarmError("work_address_kind must be numeric or letter")
    return value.strip().lower()


def work_address_parent(value: Any) -> str:
    return work_address(value, "work_address_parent")


def dependency_types(value: Any, dependencies: Sequence[str]) -> dict[str, str]:
    """Normalize dependency evidence without dropping its semantic type."""

    if value is None:
        raw: Mapping[str, Any] = {}
    elif isinstance(value, Mapping):
        raw = value
    else:
        raise DevFarmError("dependency_types must be an object")
    dependency_set = set(dependencies)
    unknown = set(raw) - dependency_set
    if unknown:
        raise DevFarmError(f"dependency_types references unknown dependency: {sorted(unknown)[0]}")
    normalized: dict[str, str] = {}
    for dependency in dependencies:
        dependency_type = raw.get(dependency, "CODE_INTEGRATED")
        if not isinstance(dependency_type, str) or dependency_type.strip().upper() not in SUPPORTED_DEPENDENCY_TYPES:
            raise DevFarmError(
                f"unsupported Commander dependency type for {dependency}: {dependency_type!r}; "
                "only CODE_INTEGRATED is currently supported"
            )
        normalized[dependency] = dependency_type.strip().upper()
    return normalized


def timestamp(value: Any, name: str) -> str:
    result = text(value, name, max_length=80)
    try:
        parsed = datetime.fromisoformat(result)
    except ValueError as exc:
        raise DevFarmError(f"{name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise DevFarmError(f"{name} must include a timezone")
    return result


def dependency_records(value: Any, tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if value is None:
        return [{"task_id": task["task_id"], "depends_on": list(task["dependencies"])} for task in tasks]
    if not isinstance(value, list):
        raise DevFarmError("dependencies must be a list")
    records: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise DevFarmError("dependency records must be objects")
        task_id = text(item.get("task_id"), "dependency task_id", max_length=101)
        depends_on = item.get("depends_on", item.get("dependencies", []))
        if not isinstance(depends_on, list):
            raise DevFarmError("dependency depends_on must be a list")
        normalized = []
        for dependency in depends_on:
            dependency_id = text(dependency, "dependency id", max_length=101)
            if dependency_id not in normalized:
                normalized.append(dependency_id)
        records.append({"task_id": task_id, "depends_on": normalized})
    return records


def ownership_records(value: Any, tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
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
                "task_id": text(item.get("task_id"), "ownership task_id", max_length=101),
                "paths": paths(item.get("paths", []), "ownership paths"),
            }
        )
    return records


def assignment_record(item: Mapping[str, Any], task: Mapping[str, Any]) -> dict[str, Any]:
    owner = text(item.get("owner", task["owner"]), "assignment owner", max_length=16).lower()
    if owner not in OWNERS:
        raise DevFarmError("assignment owner must be codex or worker")
    if owner != task["owner"]:
        raise DevFarmError(f"assignment owner does not match task owner: {task['task_id']}")
    record: dict[str, Any] = {"task_id": task["task_id"], "owner": owner}
    for key in ("provider_id", "provider_binding_id", "model_id"):
        value = item.get(key)
        if value is not None:
            record[key] = text(value, f"assignment {key}")
    if owner == "worker" and (not record.get("provider_id") or not record.get("model_id")):
        raise DevFarmError(f"worker assignment requires provider_id and model_id: {task['task_id']}")
    return record


def assignment_records(value: Any, tasks: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
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
            task_id = text(item.get("task_id"), "assignment task_id", max_length=101)
            if task_id not in by_task:
                raise DevFarmError(f"assignment references unknown task: {task_id}")
            if task_id in seen:
                raise DevFarmError(f"assignments must not contain duplicate task ids: {task_id}")
            seen.add(task_id)
            by_task[task_id] = item
    return [assignment_record(by_task[task["task_id"]], task) for task in tasks]


def check_unique_ids(values: Sequence[str], name: str) -> None:
    if len(values) != len(set(values)):
        raise DevFarmError(f"{name} must not contain duplicate task ids")


def ownership_conflict(left_path: str, right_path: str) -> bool:
    left = PurePosixPath(left_path)
    right = PurePosixPath(right_path)
    return left == right or left in right.parents or right in left.parents


def check_ownership(records: Sequence[Mapping[str, Any]]) -> None:
    seen: list[tuple[str, str]] = []
    for record in records:
        task_id = str(record["task_id"])
        for path_value in record.get("paths", []):
            if is_protected_path(path_value):
                raise DevFarmError(f"protected path cannot be owned by a plan task: {path_value}")
            for previous_task, previous_path in seen:
                if ownership_conflict(previous_path, path_value):
                    raise DevFarmError(
                        f"ownership paths overlap between {previous_task} and {task_id}: "
                        f"{previous_path}, {path_value}"
                    )
            seen.append((task_id, path_value))


def check_dependency_cycles(tasks: Sequence[Mapping[str, Any]]) -> None:
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
    """Normalize and validate a Commander parent plan."""

    if not isinstance(value, Mapping):
        raise DevFarmError("plan must be an object")
    if value.get("schema_version", PLAN_SCHEMA_VERSION) != PLAN_SCHEMA_VERSION:
        raise DevFarmError("unsupported Commander plan schema version")
    run_id = plan_id(value.get("run_id"))
    objective = text(value.get("objective"), "objective", max_length=4000)
    base_revision = revision(value.get("base_revision"))
    raw_tasks = value.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise DevFarmError("plan tasks must be a non-empty list")
    root_path = Path(root).resolve() if root is not None else None
    tasks: list[dict[str, Any]] = []
    task_ids: list[str] = []
    for raw in raw_tasks:
        if not isinstance(raw, Mapping):
            raise DevFarmError("plan tasks must be objects")
        task_id = text(raw.get("task_id"), "task_id", max_length=101)
        if not PLAN_ID_PATTERN.fullmatch(task_id):
            raise DevFarmError("task_id contains unsafe characters")
        task_ids.append(task_id)
        owner = text(raw.get("owner"), "owner", max_length=16).lower()
        if owner not in OWNERS:
            raise DevFarmError("owner must be codex or worker")
        status = text(raw.get("status", "PLANNED"), "task status", max_length=32).upper()
        if status not in PLAN_STATUSES:
            raise DevFarmError(f"unsupported task status: {status}")
        task_type = text(raw.get("task_type", "worker"), "task_type", max_length=64).lower()
        risk = text(raw.get("risk", "normal"), "risk", max_length=16).lower()
        if risk not in TASK_RISKS:
            raise DevFarmError(f"unsupported task risk: {risk}")
        sensitivity = text(raw.get("sensitivity", "normal"), "sensitivity", max_length=16).lower()
        if sensitivity not in TASK_SENSITIVITIES:
            raise DevFarmError(f"unsupported task sensitivity: {sensitivity}")
        dependencies = raw.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise DevFarmError("task dependencies must be a list")
        normalized_dependencies = []
        for dependency in dependencies:
            dependency_id = text(dependency, "dependency id", max_length=101)
            if dependency_id not in normalized_dependencies:
                normalized_dependencies.append(dependency_id)
        if task_id in normalized_dependencies:
            raise DevFarmError("a task cannot depend on itself")
        dependency_type_values = dependency_types(raw.get("dependency_types"), normalized_dependencies)
        ownership = paths(raw.get("ownership", []), "task ownership")
        manifest_path = raw.get("manifest_path")
        normalized_manifest_path = None if manifest_path is None else path(manifest_path, "manifest_path")
        if owner == "worker":
            if normalized_manifest_path is None:
                raise DevFarmError(f"worker task requires manifest_path: {task_id}")
            if root_path is not None:
                manifest_file = repository_path(root_path, normalized_manifest_path, required_parent=".devfarm/tasks")
                manifest = validate_manifest(read_json(manifest_file))
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
        raw_address = raw.get("work_address")
        parsed_address: WorkAddress | None = None
        if raw_address is not None:
            parsed_address = WorkAddress.parse(raw_address)
        address_kind = work_address_kind(
            raw.get("work_address_kind"),
            default=("letter" if parsed_address is not None and parsed_address.segments[-1].isalpha() else "numeric"),
        )
        if parsed_address is not None:
            actual_kind = "letter" if parsed_address.segments[-1].isalpha() else "numeric"
            if actual_kind != address_kind:
                raise DevFarmError("work_address_kind does not match work_address")
        address_parent = None
        if raw.get("work_address_parent") is not None:
            address_parent = work_address_parent(raw["work_address_parent"])
            if parsed_address is not None and str(parsed_address.parent) != address_parent:
                raise DevFarmError("work_address_parent does not match work_address")
        task: dict[str, Any] = {
            "task_id": task_id,
            "owner": owner,
            "status": status,
            "task_type": task_type,
            "risk": risk,
            "sensitivity": sensitivity,
            "dependencies": normalized_dependencies,
            "dependency_types": dependency_type_values,
            "ownership": ownership,
            "manifest_path": normalized_manifest_path,
            "max_attempts": max_attempts,
            "attempt_count": attempt_count,
            "work_address_kind": address_kind,
            "node_type": "task",
        }
        if parsed_address is not None:
            task["work_address"] = str(parsed_address)
        if address_parent is not None:
            task["work_address_parent"] = address_parent
        worker_candidate = raw.get("worker_candidate", owner == "worker")
        if not isinstance(worker_candidate, bool):
            raise DevFarmError("worker_candidate must be a boolean")
        task["worker_candidate"] = worker_candidate
        explicit_delegation_reason = raw.get("delegation_reason")
        if owner == "codex" and worker_candidate:
            if (
                not isinstance(explicit_delegation_reason, str)
                or not explicit_delegation_reason.strip()
                or explicit_delegation_reason.strip() == "legacy_plan_reason_not_recorded"
            ):
                raise DevFarmError("Codex worker-candidate task requires an explicit delegation_reason")
        task["delegation_reason"] = text(
            raw.get("delegation_reason", "worker_assignment" if owner == "worker" else "legacy_plan_reason_not_recorded"),
            "delegation_reason",
            max_length=1000,
        )
        if raw.get("node_type") is not None:
            node_type = text(raw["node_type"], "node_type", max_length=16).lower()
            if node_type not in {"task", "step"}:
                raise DevFarmError("node_type must be task or step")
            task["node_type"] = node_type
        assignment = raw.get("assignment", {})
        if assignment is not None:
            if not isinstance(assignment, Mapping):
                raise DevFarmError("task assignment must be an object")
            task["assignment"] = dict(assignment)
        if raw.get("result_ref") is not None:
            task["result_ref"] = path(raw["result_ref"], "result_ref")
        if raw.get("last_attempt_id") is not None:
            task["last_attempt_id"] = text(raw["last_attempt_id"], "last_attempt_id", max_length=101)
        if raw.get("stale_result_attempt_id") is not None:
            task["stale_result_attempt_id"] = text(raw["stale_result_attempt_id"], "stale_result_attempt_id", max_length=101)
        if raw.get("block_reason") is not None:
            task["block_reason"] = text(raw["block_reason"], "block_reason", max_length=1000)
        if raw.get("last_result_status") is not None:
            task["last_result_status"] = text(raw["last_result_status"], "last_result_status", max_length=32)
        if raw.get("last_error") is not None:
            task["last_error"] = text(raw["last_error"], "last_error", max_length=1000)
        if raw.get("integration_note") is not None:
            task["integration_note"] = text(raw["integration_note"], "integration_note", max_length=2000)
        if raw.get("manifest_history") is not None:
            task["manifest_history"] = paths(raw["manifest_history"], "manifest_history")
        for key in ("target_ref", "integration_revision", "source_attempt_id"):
            if raw.get(key) is not None:
                task[key] = text(raw[key], key, max_length=200)
        if raw.get("verified_patch_digest") is not None:
            digest = text(raw["verified_patch_digest"], "verified_patch_digest", max_length=64).lower()
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise DevFarmError("verified_patch_digest must be a SHA-256 hex digest")
            task["verified_patch_digest"] = digest
        if raw.get("dispatch_id") is not None:
            task["dispatch_id"] = text(raw["dispatch_id"], "dispatch_id", max_length=101)
        for key in ("dispatch_started_at", "dispatch_deadline_at"):
            if raw.get(key) is not None:
                task[key] = timestamp(raw[key], key)
        if raw.get("dispatch_owner_pid") is not None:
            owner_pid = raw["dispatch_owner_pid"]
            if isinstance(owner_pid, bool) or not isinstance(owner_pid, int) or owner_pid <= 0:
                raise DevFarmError("dispatch_owner_pid must be a positive integer")
            task["dispatch_owner_pid"] = owner_pid
        if raw.get("dispatch_recovery") is not None:
            task["dispatch_recovery"] = text(raw["dispatch_recovery"], "dispatch_recovery", max_length=128)
        tasks.append(task)
    check_unique_ids(task_ids, "plan tasks")
    existing_addresses = [task["work_address"] for task in tasks if task.get("work_address") is not None]
    for task in tasks:
        if task.get("work_address") is not None:
            continue
        allocated = allocate_work_address(task.get("work_address_parent"), existing_addresses, kind=task["work_address_kind"])
        task["work_address"] = str(allocated)
        existing_addresses.append(str(allocated))
    work_addresses = [task["work_address"] for task in tasks if task.get("work_address") is not None]
    if len(work_addresses) != len(set(work_addresses)):
        raise DevFarmError("work_address values must be unique within a plan")
    task_id_set = set(task_ids)

    dependencies = dependency_records(value.get("dependencies"), tasks)
    dependency_task_ids = [str(record["task_id"]) for record in dependencies]
    check_unique_ids(dependency_task_ids, "dependencies")
    dependency_by_task = {record["task_id"]: record["depends_on"] for record in dependencies}
    if set(dependency_by_task) != task_id_set:
        raise DevFarmError("dependencies must contain exactly one record for every task")
    for task in tasks:
        normalized = list(dependency_by_task[task["task_id"]])
        if any(dependency not in task_id_set for dependency in normalized):
            raise DevFarmError(f"dependency references an unknown task: {task['task_id']}")
        task["dependencies"] = normalized
    dependencies = [
        {
            "task_id": task["task_id"],
            "depends_on": list(task["dependencies"]),
            "dependency_types": dict(task["dependency_types"]),
        }
        for task in tasks
    ]
    check_dependency_cycles(tasks)

    ownership = ownership_records(value.get("ownership"), tasks)
    ownership_task_ids = [str(record["task_id"]) for record in ownership]
    check_unique_ids(ownership_task_ids, "ownership")
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
    check_ownership(ownership)

    assignments = assignment_records(value.get("assignments"), tasks)
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
            "task_id": text(raw_result.get("task_id"), "result task_id", max_length=101),
            "stage": text(raw_result.get("stage"), "result stage", max_length=64),
            "status": text(raw_result.get("status"), "result status", max_length=32),
        }
        if result_record["task_id"] not in task_id_set:
            raise DevFarmError(f"result references unknown task: {result_record['task_id']}")
        if raw_result.get("result_ref") is not None:
            result_record["result_ref"] = path(raw_result["result_ref"], "result_ref")
        if raw_result.get("attempt_id") is not None:
            result_record["attempt_id"] = text(raw_result["attempt_id"], "result attempt_id", max_length=101)
        if raw_result.get("recorded_at") is not None:
            result_record["recorded_at"] = text(raw_result["recorded_at"], "recorded_at", max_length=80)
        normalized_results.append(result_record)

    review_decisions = value.get("review_decisions", [])
    if not isinstance(review_decisions, list):
        raise DevFarmError("review_decisions must be a list")
    normalized_review_decisions = [normalize_review_decision(item) for item in review_decisions]
    decision_ids = [item["decision_id"] for item in normalized_review_decisions]
    check_unique_ids(decision_ids, "review_decisions")

    plan_status = text(value.get("status", "PLANNED"), "plan status", max_length=32).upper()
    if plan_status not in PLAN_STATUSES:
        raise DevFarmError(f"unsupported plan status: {plan_status}")
    plan_revision = value.get("plan_revision", 0)
    if isinstance(plan_revision, bool) or not isinstance(plan_revision, int) or plan_revision < 0:
        raise DevFarmError("plan_revision must be a non-negative integer")
    return {
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
        "review_decisions": normalized_review_decisions,
        "plan_revision": plan_revision,
        "created_at": text(value.get("created_at", now()), "created_at", max_length=80),
        "updated_at": text(value.get("updated_at", now()), "updated_at", max_length=80),
        "supervisor": normalize_supervisor_metadata(value.get("supervisor")),
    }


def summarize_delegation(value: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize plan ownership without inferring unrecorded implementation."""

    if not isinstance(value, Mapping):
        raise DevFarmError("plan must be an object")
    tasks = value.get("tasks")
    if not isinstance(tasks, list):
        raise DevFarmError("plan tasks must be a list")
    worker_owned = 0
    codex_owned = 0
    worker_integrated = 0
    codex_direct = 0
    direct_reasons: set[str] = set()
    for task in tasks:
        if not isinstance(task, Mapping):
            raise DevFarmError("plan tasks must be objects")
        owner = task.get("owner")
        if owner not in OWNERS:
            raise DevFarmError("plan task owner must be codex or worker")
        status = task.get("status")
        if not isinstance(status, str) or not status.strip():
            raise DevFarmError("plan task status must be a non-empty string")
        if owner == "worker":
            worker_owned += 1
            if status.upper() == "INTEGRATED":
                worker_integrated += 1
            continue
        codex_owned += 1
        if task.get("worker_candidate") is True:
            codex_direct += 1
            reason = task.get("delegation_reason")
            if not isinstance(reason, str) or not reason.strip():
                raise DevFarmError("Codex direct task requires delegation_reason")
            direct_reasons.add(reason.strip())
    return {
        "worker_owned_task_count": worker_owned,
        "codex_owned_task_count": codex_owned,
        "worker_integrated_task_count": worker_integrated,
        "codex_direct_implementation_count": codex_direct,
        "codex_direct_reasons": sorted(direct_reasons),
    }


__all__ = [
    "ACTIVE_TASK_STATUSES",
    "DEPENDENCY_COMPLETE",
    "DEPENDENCY_FAILURE",
    "OWNERS",
    "PLAN_ID_PATTERN",
    "PLAN_SCHEMA_VERSION",
    "PLAN_STATUSES",
    "SUPPORTED_DEPENDENCY_TYPES",
    "summarize_delegation",
    "validate_plan",
]
