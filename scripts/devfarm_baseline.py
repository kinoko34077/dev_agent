"""Read-only W0 baseline projections for weak-model compensation.

The baseline boundary deliberately consumes existing DevFarm manifests,
attempt results, verification records, and (when available) Commander plan
projections.  It never dispatches a provider, mutates a plan, changes routing,
or treats a proposal claim as Host evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
import re
from typing import Any

from scripts.devfarm_contracts import parse_host_test_command
from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_artifacts import list_verification_records
from scripts.devfarm_repository import read_bounded_json, repository_path


class BaselineError(DevFarmError):
    """A baseline fixture or observation is malformed."""


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
_TASK_CLASSES = frozenset(
    {
        "localized_existing_source",
        "small_new_file_or_docs",
        "syntax_import_repair",
        "schema_contract_violation",
    }
)
_EVIDENCE_CLASSES = frozenset({"fresh_runtime", "historical_replay", "synthetic_fixture"})
_OUTPUT_MODES = frozenset({"full_result", "minimal_file_replacement"})
_PATH_MAX = 512
_TEXT_MAX = 4_000


def _text(value: Any, name: str, *, maximum: int = _TEXT_MAX) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise BaselineError(f"{name} must be bounded non-empty text")
    return value.strip()


def _safe_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value.strip()):
        raise BaselineError(f"{name} must be a safe identifier")
    return value.strip()


def _paths(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise BaselineError(f"{name} must be a sequence")
    normalized: list[str] = []
    for item in value:
        path = _text(item, f"{name}[]", maximum=_PATH_MAX).replace("\\", "/")
        if path.startswith(("/", "./")) or ":" in path or ".." in path.split("/"):
            raise BaselineError(f"{name} contains an unsafe path")
        if path not in normalized:
            normalized.append(path)
    return tuple(normalized)


def _strings(value: Any, name: str, *, maximum_items: int = 32) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise BaselineError(f"{name} must be a sequence")
    if len(value) > maximum_items:
        raise BaselineError(f"{name} contains too many items")
    normalized = tuple(_text(item, f"{name}[]") for item in value)
    if len(set(normalized)) != len(normalized):
        raise BaselineError(f"{name} must not contain duplicates")
    return normalized


@dataclass(frozen=True)
class BaselineFixture:
    """One pinned, reusable goal-relevant task shape."""

    fixture_id: str
    task_class: str
    objective: str
    allowed_files: tuple[str, ...]
    read_files: tuple[str, ...]
    outbound_files: tuple[str, ...]
    new_files: tuple[str, ...]
    acceptance: tuple[str, ...]
    test_commands: tuple[str, ...]
    approved_provider_ids: tuple[str, ...]
    output_modes: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BaselineFixture":
        if not isinstance(value, Mapping):
            raise BaselineError("baseline fixture must be an object")
        allowed = {
            "fixture_id", "task_class", "objective", "allowed_files", "read_files",
            "outbound_files", "new_files", "acceptance", "test_commands",
            "approved_provider_ids", "output_modes",
        }
        unknown = set(value) - allowed
        if unknown:
            raise BaselineError(f"baseline fixture has unknown field: {sorted(unknown)[0]}")
        fixture_id = _safe_id(value.get("fixture_id"), "fixture_id")
        task_class = _text(value.get("task_class"), "task_class", maximum=80)
        if task_class not in _TASK_CLASSES:
            raise BaselineError(f"task_class is unsupported: {task_class}")
        allowed_files = _paths(value.get("allowed_files"), "allowed_files")
        read_files = _paths(value.get("read_files"), "read_files")
        outbound_files = _paths(value.get("outbound_files"), "outbound_files")
        new_files = _paths(value.get("new_files", []), "new_files")
        if not set(outbound_files).issubset(set(allowed_files) | set(read_files)):
            raise BaselineError("outbound_files must stay within the readable allowed scope")
        if set(new_files) & (set(read_files) | set(outbound_files)):
            raise BaselineError("new_files must not overlap read_files or outbound_files")
        if not set(new_files).issubset(set(allowed_files)):
            raise BaselineError("new_files must stay within allowed_files")
        acceptance = _strings(value.get("acceptance"), "acceptance")
        test_commands = _strings(value.get("test_commands"), "test_commands")
        for command in test_commands:
            try:
                parse_host_test_command(command)
            except DevFarmError as exc:
                raise BaselineError(f"invalid fixture test command: {command}") from exc
        providers = _strings(value.get("approved_provider_ids"), "approved_provider_ids")
        output_modes = _strings(value.get("output_modes"), "output_modes")
        if any(mode not in _OUTPUT_MODES for mode in output_modes):
            raise BaselineError("output_modes contains an unsupported mode")
        if not output_modes:
            raise BaselineError("output_modes must not be empty")
        return cls(
            fixture_id=fixture_id,
            task_class=task_class,
            objective=_text(value.get("objective"), "objective"),
            allowed_files=allowed_files,
            read_files=read_files,
            outbound_files=outbound_files,
            new_files=new_files,
            acceptance=acceptance,
            test_commands=test_commands,
            approved_provider_ids=providers,
            output_modes=output_modes,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id,
            "task_class": self.task_class,
            "objective": self.objective,
            "allowed_files": list(self.allowed_files),
            "read_files": list(self.read_files),
            "outbound_files": list(self.outbound_files),
            "new_files": list(self.new_files),
            "acceptance": list(self.acceptance),
            "test_commands": list(self.test_commands),
            "approved_provider_ids": list(self.approved_provider_ids),
            "output_modes": list(self.output_modes),
        }


@dataclass(frozen=True)
class BaselineFixtureSet:
    baseline_id: str
    source_revision: str
    fixtures: tuple[BaselineFixture, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "baseline_id": self.baseline_id,
            "source_revision": self.source_revision,
            "fixtures": [item.to_dict() for item in self.fixtures],
        }


def load_fixture_set(value: Mapping[str, Any]) -> BaselineFixtureSet:
    if not isinstance(value, Mapping):
        raise BaselineError("baseline fixture set must be an object")
    allowed = {
        "schema_version",
        "evidence_kind",
        "evidence_status",
        "observed_at",
        "implementation_baseline",
        "baseline_id",
        "source_revision",
        "fixtures",
        "scope_boundary",
    }
    unknown = set(value) - allowed
    if unknown:
        raise BaselineError(f"baseline fixture set has unknown field: {sorted(unknown)[0]}")
    if value.get("schema_version") != 1:
        raise BaselineError("baseline fixture set schema_version must be 1")
    if value.get("evidence_kind") not in {
        None,
        "weak_model_compensation_baseline_fixture_definition",
    }:
        raise BaselineError("baseline fixture set evidence_kind is unsupported")
    if value.get("evidence_status") not in {None, "synthetic_fixture_only"}:
        raise BaselineError("baseline fixture set evidence_status must be synthetic_fixture_only")
    baseline_id = _safe_id(value.get("baseline_id"), "baseline_id")
    source_revision = _text(value.get("source_revision"), "source_revision", maximum=200)
    implementation_baseline = value.get("implementation_baseline")
    if implementation_baseline is not None and implementation_baseline != source_revision:
        raise BaselineError("implementation_baseline must match source_revision")
    raw_fixtures = value.get("fixtures")
    if isinstance(raw_fixtures, (str, bytes)) or not isinstance(raw_fixtures, Sequence) or not raw_fixtures:
        raise BaselineError("fixtures must be a non-empty sequence")
    fixtures: list[BaselineFixture] = []
    for raw in raw_fixtures:
        if not isinstance(raw, Mapping):
            raise BaselineError("fixtures must contain objects")
        declared_revision = raw.get("source_revision")
        if declared_revision is not None and declared_revision != source_revision:
            raise BaselineError("fixture source_revision does not match baseline source_revision")
        fixture = BaselineFixture.from_dict(raw)
        if any(existing.fixture_id == fixture.fixture_id for existing in fixtures):
            raise BaselineError("fixture_id must be unique")
        fixtures.append(fixture)
    return BaselineFixtureSet(baseline_id, source_revision, tuple(fixtures))


def _failure_class(result: Mapping[str, Any], task: Mapping[str, Any] | None) -> str:
    spec = result.get("failure_spec")
    if isinstance(spec, Mapping) and isinstance(spec.get("failure_class"), str):
        return spec["failure_class"].strip().upper()
    error = " ".join(
        str(value).casefold()
        for value in (result.get("known_issues"), (task or {}).get("last_error"))
        if value is not None
    )
    if "json" in error or "schema" in error or "proposal" in error:
        return "FORMAT_PATCH"
    if "reconciliation" in error or "unknown" in error:
        return "UNKNOWN"
    if "provider" in error or "transport" in error:
        return "PROVIDER"
    if "syntax" in error or "import" in error or "test" in error:
        return "SEMANTIC_TEST"
    return "NONE"


def _repair_stage(manifest: Mapping[str, Any]) -> str:
    """Classify the bounded correction stage from Host-owned handoff facts."""

    handoff = manifest.get("rework_handoff")
    if not isinstance(handoff, Mapping):
        return "initial"
    contexts: list[Mapping[str, Any]] = []
    for candidate in (
        handoff.get("repair_context"),
        handoff.get("payload_reference"),
        handoff.get("references"),
    ):
        if not isinstance(candidate, Mapping):
            continue
        if candidate.get("repair_context") is not None and isinstance(candidate.get("repair_context"), Mapping):
            contexts.append(candidate["repair_context"])
        else:
            contexts.append(candidate)
    is_fallback = any(
        isinstance(context.get("fallback_from_provider"), str)
        or isinstance(context.get("fallback_to_provider"), str)
        for context in contexts
    )
    is_host = any(
        context.get("host_verification_rework") is True
        or (
            isinstance(context.get("latest_failure_spec"), Mapping)
            and context["latest_failure_spec"].get("stage") == "host_verification"
        )
        for context in contexts
    )
    if is_host and is_fallback:
        return "host_fallback"
    if is_host:
        return "host_repair"
    if is_fallback:
        return "format_fallback"
    return "format_repair"


def _task_from_plan(root: Path, task_id: str) -> Mapping[str, Any] | None:
    plan_root = root / ".devfarm" / "plans"
    if not plan_root.is_dir():
        return None
    matches: list[Mapping[str, Any]] = []
    for path in sorted(plan_root.glob("*.json")):
        try:
            plan = read_bounded_json(root, path, maximum_chars=250_000)
        except DevFarmError:
            continue
        tasks = plan.get("tasks") if isinstance(plan, Mapping) else None
        if not isinstance(tasks, list):
            continue
        for task in tasks:
            if isinstance(task, Mapping) and task.get("task_id") == task_id:
                matches.append(task)
    if not matches:
        return None
    return matches[-1]


def _usage(metrics: Mapping[str, Any]) -> dict[str, int]:
    usage = metrics.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    return {
        "prompt_tokens": next(
            (int(usage[key]) for key in ("prompt_tokens", "input_tokens") if isinstance(usage.get(key), int) and usage[key] >= 0),
            0,
        ),
        "completion_tokens": next(
            (int(usage[key]) for key in ("completion_tokens", "output_tokens") if isinstance(usage.get(key), int) and usage[key] >= 0),
            0,
        ),
        "cost_minor": int(usage.get("cost_minor", 0)) if isinstance(usage.get("cost_minor"), int) and usage.get("cost_minor") >= 0 else 0,
    }


def collect_attempt_observation(
    root: str | Path,
    *,
    task_id: str,
    attempt_id: str,
    task_class: str,
    evidence_class: str,
) -> dict[str, Any]:
    """Project one attempt without treating absent evidence as success."""

    root_path = Path(root).resolve()
    task_id = _safe_id(task_id, "task_id")
    attempt_id = _safe_id(attempt_id, "attempt_id")
    if task_class not in _TASK_CLASSES:
        raise BaselineError("task_class is unsupported")
    if evidence_class not in _EVIDENCE_CLASSES:
        raise BaselineError("evidence_class is unsupported")
    manifest_path = repository_path(root_path, f".devfarm/tasks/{task_id}.json", required_parent=".devfarm/tasks")
    manifest = read_bounded_json(root_path, manifest_path, maximum_chars=250_000)
    if not isinstance(manifest, Mapping):
        raise BaselineError("Worker manifest must be an object")
    result_path = repository_path(
        root_path,
        f".devfarm/results/{task_id}/attempts/{attempt_id}/result.json",
        required_parent=".devfarm/results",
    )
    result = read_bounded_json(root_path, result_path, maximum_chars=250_000)
    if not isinstance(result, Mapping):
        raise BaselineError("Worker result must be an object")
    metrics = result.get("worker_metrics")
    metrics = metrics if isinstance(metrics, Mapping) else {}
    preflight = metrics.get("proposal_preflight")
    proposal_parse = (
        "accepted"
        if isinstance(preflight, Mapping) and preflight.get("status") == "accepted"
        else "rejected"
        if result.get("failure_spec") is not None or result.get("status") == "failed"
        else "not_observed"
    )
    verifications = list_verification_records(root_path, task_id, attempt_id)
    verification_passed = any(
        record.get("independent_verification") is True
        and isinstance(record.get("verified_tests"), list)
        and bool(record.get("verified_tests"))
        and all(isinstance(test, Mapping) and test.get("passed") is True for test in record["verified_tests"])
        for record in verifications
    )
    host_verification = "passed" if verification_passed else "failed" if verifications else "not_observed"
    accepted = bool(metrics.get("result_accepted") is True and host_verification == "passed")
    task = _task_from_plan(root_path, task_id)
    reviewed = None if task is None else bool(task.get("review_decision_id"))
    integrated = None if task is None else bool(task.get("integration_revision"))
    usage = _usage(metrics)
    attempt_count = metrics.get("attempt_count") if isinstance(metrics.get("attempt_count"), int) else 1
    retry_count = metrics.get("retry_count") if isinstance(metrics.get("retry_count"), int) else max(attempt_count - 1, 0)
    elapsed_ms = metrics.get("elapsed_ms") if isinstance(metrics.get("elapsed_ms"), int) and metrics.get("elapsed_ms") >= 0 else None
    return {
        "task_id": task_id,
        "attempt_id": attempt_id,
        "task_class": task_class,
        "evidence_class": evidence_class,
        "source_revision": manifest.get("base_revision"),
        "output_mode": (manifest.get("output_contract") or {}).get("mode") if isinstance(manifest.get("output_contract"), Mapping) else None,
        "repair_stage": _repair_stage(manifest),
        "provider_id": metrics.get("provider_id"),
        "provider_binding_id": metrics.get("provider_binding_id"),
        "model_id": metrics.get("model_id"),
        "proposal_parse": proposal_parse,
        "host_verification": host_verification,
        "reviewed": reviewed,
        "integrated": integrated,
        "accepted": accepted,
        "failure_class": _failure_class(result, task),
        "attempt_count": attempt_count,
        "retry_count": retry_count,
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "cost_minor": usage["cost_minor"],
        "elapsed_ms": elapsed_ms,
        "host_execution_ms": metrics.get("host_execution_ms") if isinstance(metrics.get("host_execution_ms"), int) else None,
        "human_intervention_count": metrics.get("human_intervention_count") if isinstance(metrics.get("human_intervention_count"), int) else None,
        "verification_count": len(verifications),
    }


def summarize_observations(
    *,
    baseline_id: str,
    source_revision: str,
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate comparable W0 outcomes while retaining unknown measures."""

    baseline_id = _safe_id(baseline_id, "baseline_id")
    source_revision = _text(source_revision, "source_revision", maximum=200)
    rows = [dict(item) for item in observations]
    def count(predicate: Any) -> int:
        return sum(1 for item in rows if predicate(item))
    totals = {
        "prompt_tokens": sum(item.get("prompt_tokens", 0) for item in rows if isinstance(item.get("prompt_tokens"), int)),
        "completion_tokens": sum(item.get("completion_tokens", 0) for item in rows if isinstance(item.get("completion_tokens"), int)),
        "cost_minor": sum(item.get("cost_minor", 0) for item in rows if isinstance(item.get("cost_minor"), int)),
        "elapsed_ms": sum(item.get("elapsed_ms", 0) for item in rows if isinstance(item.get("elapsed_ms"), int)),
    }
    by_task_class: dict[str, int] = {}
    by_failure_class: dict[str, int] = {}
    by_evidence_class: dict[str, int] = {}
    by_repair_stage: dict[str, int] = {}
    for item in rows:
        for field, target in (
            ("task_class", by_task_class),
            ("failure_class", by_failure_class),
            ("evidence_class", by_evidence_class),
            ("repair_stage", by_repair_stage),
        ):
            value = item.get(field)
            if isinstance(value, str):
                target[value] = target.get(value, 0) + 1
    unmeasured = sorted(
        field
        for field in ("host_execution_ms", "human_intervention_count", "reviewed", "integrated")
        if any(item.get(field) is None for item in rows)
    )
    return {
        "schema_version": 1,
        "baseline_id": baseline_id,
        "source_revision": source_revision,
        "attempts_total": len(rows),
        "proposal_parsed": count(lambda item: item.get("proposal_parse") == "accepted"),
        "host_verified": count(lambda item: item.get("host_verification") == "passed"),
        "accepted": count(lambda item: item.get("accepted") is True),
        "pre_host_failures": count(lambda item: item.get("proposal_parse") == "rejected"),
        "reviewed_observed": count(lambda item: item.get("reviewed") is True),
        "integrated_observed": count(lambda item: item.get("integrated") is True),
        "totals": totals,
        "by_task_class": by_task_class,
        "by_failure_class": by_failure_class,
        "by_evidence_class": by_evidence_class,
        "by_repair_stage": by_repair_stage,
        "unmeasured": unmeasured,
        "observations": rows,
    }


__all__ = [
    "BaselineError",
    "BaselineFixture",
    "BaselineFixtureSet",
    "collect_attempt_observation",
    "load_fixture_set",
    "summarize_observations",
]
