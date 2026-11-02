"""Read-only W0 baseline projections for weak-model compensation.

The baseline boundary deliberately consumes existing DevFarm manifests,
attempt results, verification records, and (when available) Commander plan
projections.  It never dispatches a provider, mutates a plan, changes routing,
or treats a proposal claim as Host evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
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
_OUTPUT_MODES = frozenset({"full_result", "minimal_file_replacement", "localized_file_edit"})
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


def _task_fingerprint(manifest: Mapping[str, Any]) -> str:
    """Hash task/source/acceptance constraints while excluding model choice."""

    fields = (
        "task_type",
        "objective",
        "base_revision",
        "allowed_files",
        "read_files",
        "forbidden_files",
        "outbound_files",
        "new_files",
        "requirements",
        "acceptance",
        "test_commands",
    )
    payload = {key: manifest.get(key) for key in fields}
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    metrics = result.get("worker_metrics")
    if isinstance(metrics, Mapping) and metrics.get("pre_dispatch_block") == "feasibility":
        return "FEASIBILITY"
    error = " ".join(
        str(value).casefold()
        for value in (result.get("known_issues"), (task or {}).get("last_error"))
        if value is not None
    )
    if "feasibility" in error or "reframe_required" in error:
        return "FEASIBILITY"
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
        "task_fingerprint": _task_fingerprint(manifest),
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


def build_baseline_report(root: str | Path, selection: Mapping[str, Any]) -> dict[str, Any]:
    """Build a read-only W0 report from an explicit attempt selection.

    The caller must provide every task/attempt identity and its evidence class;
    this function never discovers candidates by scanning filenames or infers a
    task class from an artifact path.  All selected attempts must share the
    declared source revision so that the resulting denominator is reproducible.
    """

    if not isinstance(selection, Mapping):
        raise BaselineError("baseline report selection must be an object")
    allowed = {"schema_version", "report_id", "baseline_id", "source_revision", "observations"}
    unknown = set(selection) - allowed
    if unknown:
        raise BaselineError(f"baseline report selection has unknown field: {sorted(unknown)[0]}")
    if selection.get("schema_version") != 1:
        raise BaselineError("baseline report selection schema_version must be 1")
    report_id = _safe_id(selection.get("report_id"), "report_id")
    baseline_id = _safe_id(selection.get("baseline_id"), "baseline_id")
    source_revision = _text(selection.get("source_revision"), "source_revision", maximum=64)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", source_revision):
        raise BaselineError("source_revision must be a 40-character commit revision")
    raw_observations = selection.get("observations")
    if isinstance(raw_observations, (str, bytes)) or not isinstance(raw_observations, Sequence):
        raise BaselineError("observations must be a sequence")
    if not raw_observations:
        raise BaselineError("observations must not be empty")
    if len(raw_observations) > 128:
        raise BaselineError("observations contains too many attempts")

    observations: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    observation_fields = {"task_id", "attempt_id", "task_class", "evidence_class"}
    for index, raw in enumerate(raw_observations):
        if not isinstance(raw, Mapping):
            raise BaselineError(f"observations[{index}] must be an object")
        extra = set(raw) - observation_fields
        if extra:
            raise BaselineError(f"observations[{index}] has unknown field: {sorted(extra)[0]}")
        task_id = _safe_id(raw.get("task_id"), f"observations[{index}].task_id")
        attempt_id = _safe_id(raw.get("attempt_id"), f"observations[{index}].attempt_id")
        identity = (task_id, attempt_id)
        if identity in seen:
            raise BaselineError("observations must not contain duplicate task/attempt identities")
        seen.add(identity)
        task_class = raw.get("task_class")
        evidence_class = raw.get("evidence_class")
        if task_class not in _TASK_CLASSES:
            raise BaselineError(f"observations[{index}].task_class is unsupported")
        if evidence_class not in _EVIDENCE_CLASSES:
            raise BaselineError(f"observations[{index}].evidence_class is unsupported")
        try:
            observation = collect_attempt_observation(
                root,
                task_id=task_id,
                attempt_id=attempt_id,
                task_class=task_class,
                evidence_class=evidence_class,
            )
        except BaselineError:
            raise
        except DevFarmError as exc:
            raise BaselineError("selected observation artifact is unavailable or malformed") from exc
        if observation.get("source_revision") != source_revision:
            raise BaselineError(
                f"observations[{index}] source_revision does not match declared source_revision"
            )
        observations.append(observation)

    summary = summarize_observations(
        baseline_id=baseline_id,
        source_revision=source_revision,
        observations=observations,
    )
    return {
        "schema_version": 1,
        "evidence_kind": "weak_model_compensation_observed_baseline",
        "evidence_status": "observed_attempts",
        "report_id": report_id,
        "baseline_id": baseline_id,
        "source_revision": source_revision,
        "observation_count": len(observations),
        "summary": summary,
        "observations": observations,
        "scope_boundary": (
            "This report is measurement evidence only. It does not renew model "
            "capability, billing, quota, health, runtime admission, routing, or Gate evidence."
        ),
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


def _comparison_row(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BaselineError(f"{name} observation must be an object")
    task_id = _safe_id(value.get("task_id"), f"{name}.task_id")
    fingerprint = _text(value.get("task_fingerprint"), f"{name}.task_fingerprint", maximum=128)
    source_revision = _text(value.get("source_revision"), f"{name}.source_revision", maximum=200)
    task_class = _text(value.get("task_class"), f"{name}.task_class", maximum=64)
    proposal_parse = value.get("proposal_parse")
    if proposal_parse not in {"accepted", "rejected", "not_observed"}:
        raise BaselineError(f"{name}.proposal_parse is unsupported")
    accepted = value.get("accepted")
    if not isinstance(accepted, bool):
        raise BaselineError(f"{name}.accepted must be a boolean")
    row: dict[str, Any] = {
        "task_id": task_id,
        "task_fingerprint": fingerprint,
        "source_revision": source_revision,
        "task_class": task_class,
        "proposal_parse": proposal_parse,
        "accepted": accepted,
    }
    for field in ("host_verification", "reviewed", "integrated"):
        item = value.get(field)
        if item is not None and not isinstance(item, (bool, str)):
            raise BaselineError(f"{name}.{field} must be boolean, text, or null")
        row[field] = item
    for field in ("cost_minor", "elapsed_ms", "prompt_tokens", "completion_tokens", "retry_count"):
        item = value.get(field, 0)
        if item is None:
            row[field] = None
        elif isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise BaselineError(f"{name}.{field} must be a non-negative integer or null")
        else:
            row[field] = item
    return row


def _comparison_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def count(predicate: Any) -> int:
        return sum(1 for item in rows if predicate(item))

    return {
        "attempts": len(rows),
        "proposal_parsed": count(lambda item: item["proposal_parse"] == "accepted"),
        "host_verified": count(lambda item: item.get("host_verification") == "passed"),
        "accepted": count(lambda item: item["accepted"] is True),
        "pre_host_failures": count(lambda item: item["proposal_parse"] == "rejected"),
        "totals": {
            field: (
                sum(item[field] for item in rows if isinstance(item.get(field), int))
                if all(item.get(field) is not None for item in rows)
                else None
            )
            for field in ("prompt_tokens", "completion_tokens", "cost_minor", "elapsed_ms", "retry_count")
        },
    }


def compare_observation_cohorts(
    *,
    baseline_id: str,
    treatment_id: str,
    baseline: Sequence[Mapping[str, Any]],
    treatment: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare matched Host observations without promoting a routing claim.

    Matching uses a Host-derived task fingerprint plus source revision and task
    class.  Provider/model/output mode are deliberately excluded so a paired
    comparison can measure whether a new instruction or output contract changed
    end-to-end useful-work yield.  Every attempt, including pre-Host proposal
    failures, stays in the denominator.  Missing or duplicate pairs yield an
    insufficient-evidence result rather than an invented improvement claim.
    """

    baseline_id = _safe_id(baseline_id, "baseline_id")
    treatment_id = _safe_id(treatment_id, "treatment_id")
    if isinstance(baseline, (str, bytes)) or not isinstance(baseline, Sequence):
        raise BaselineError("baseline must be a sequence")
    if isinstance(treatment, (str, bytes)) or not isinstance(treatment, Sequence):
        raise BaselineError("treatment must be a sequence")
    baseline_rows = tuple(_comparison_row(item, "baseline") for item in baseline)
    treatment_rows = tuple(_comparison_row(item, "treatment") for item in treatment)

    def key(item: Mapping[str, Any]) -> tuple[str, str, str]:
        return item["task_fingerprint"], item["source_revision"], item["task_class"]

    def index(rows: Sequence[Mapping[str, Any]], label: str) -> dict[tuple[str, str, str], Mapping[str, Any]]:
        result: dict[tuple[str, str, str], Mapping[str, Any]] = {}
        for item in rows:
            identity = key(item)
            if identity in result:
                raise BaselineError(f"duplicate {label} task fingerprint")
            result[identity] = item
        return result

    baseline_by_key = index(baseline_rows, "baseline")
    treatment_by_key = index(treatment_rows, "treatment")
    matched_keys = tuple(sorted(set(baseline_by_key) & set(treatment_by_key)))
    unmatched_baseline = sorted(item["task_id"] for key_value, item in baseline_by_key.items() if key_value not in treatment_by_key)
    unmatched_treatment = sorted(item["task_id"] for key_value, item in treatment_by_key.items() if key_value not in baseline_by_key)
    matched_baseline = tuple(baseline_by_key[item] for item in matched_keys)
    matched_treatment = tuple(treatment_by_key[item] for item in matched_keys)
    baseline_summary = _comparison_summary(matched_baseline)
    treatment_summary = _comparison_summary(matched_treatment)
    delta = {
        "accepted": treatment_summary["accepted"] - baseline_summary["accepted"],
        "proposal_parsed": treatment_summary["proposal_parsed"] - baseline_summary["proposal_parsed"],
        "host_verified": treatment_summary["host_verified"] - baseline_summary["host_verified"],
        "pre_host_failures": treatment_summary["pre_host_failures"] - baseline_summary["pre_host_failures"],
        "totals": {
            field: (
                treatment_summary["totals"][field] - baseline_summary["totals"][field]
                if treatment_summary["totals"][field] is not None and baseline_summary["totals"][field] is not None
                else None
            )
            for field in ("prompt_tokens", "completion_tokens", "cost_minor", "elapsed_ms", "retry_count")
        },
    }
    if not matched_keys:
        conclusion = "insufficient_evidence"
        status = "INSUFFICIENT_MATCHED_DATA"
    else:
        baseline_rate = baseline_summary["accepted"] / baseline_summary["attempts"]
        treatment_rate = treatment_summary["accepted"] / treatment_summary["attempts"]
        measured_totals = delta["totals"]
        resource_not_worse = all(
            measured_totals[field] is None or measured_totals[field] <= 0
            for field in ("prompt_tokens", "completion_tokens", "cost_minor", "elapsed_ms", "retry_count")
        )
        if treatment_rate > baseline_rate and resource_not_worse:
            conclusion = "improved"
        elif treatment_rate < baseline_rate:
            conclusion = "regressed"
        elif treatment_rate > baseline_rate:
            conclusion = "mixed"
        else:
            conclusion = "neutral"
        status = "PAIRED" if not unmatched_baseline and not unmatched_treatment else "PARTIAL_MATCH"
        if status == "PARTIAL_MATCH":
            conclusion = "insufficient_evidence"
    return {
        "schema_version": 1,
        "evidence_status": "comparison_only",
        "baseline_id": baseline_id,
        "treatment_id": treatment_id,
        "status": status,
        "matched_tasks": len(matched_keys),
        "unmatched_baseline": unmatched_baseline,
        "unmatched_treatment": unmatched_treatment,
        "baseline": baseline_summary,
        "treatment": treatment_summary,
        "delta": delta,
        "conclusion": conclusion,
    }


__all__ = [
    "BaselineError",
    "BaselineFixture",
    "BaselineFixtureSet",
    "build_baseline_report",
    "compare_observation_cohorts",
    "collect_attempt_observation",
    "load_fixture_set",
    "summarize_observations",
]
