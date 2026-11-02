"""Build a read-only report for explicit Host-accepted Worker artifacts.

The report is intentionally stricter than a Worker result.  It preserves
historical Host-verified work as evidence, but only reports W6 ``PASS`` when
the selection also supplies fresh-runtime evidence, integration evidence,
matched baseline evidence, downstream selection evidence, and restart
continuity evidence.  It never scans for candidates, dispatches a Provider,
mutates a plan, or grants routing/Gate authority.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
import re
import sys
from typing import Any

if __package__ in {None, ""}:  # pragma: no cover - exercised by path entrypoint
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_repository import read_bounded_json, repository_path


class UsefulWorkReportError(DevFarmError):
    """An explicit useful-work selection is malformed or insufficient."""


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
_REVISION = re.compile(r"^[0-9a-fA-F]{40}$")
_TASK_CLASSES = frozenset(
    {
        "localized_existing_source",
        "small_new_file_or_docs",
        "syntax_import_repair",
        "schema_contract_violation",
    }
)
_EVIDENCE_CLASSES = frozenset({"historical_replay", "fresh_runtime"})
_MAX_OBSERVATIONS = 64


def _safe_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value.strip()):
        raise UsefulWorkReportError(f"{name} must be a safe identifier")
    return value.strip()


def _text(value: Any, name: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise UsefulWorkReportError(f"{name} must be bounded non-empty text")
    return value.strip()


def _revision(value: Any, name: str) -> str:
    candidate = _text(value, name, maximum=40)
    if not _REVISION.fullmatch(candidate):
        raise UsefulWorkReportError(f"{name} must be a 40-character commit revision")
    return candidate


def _result_ref(value: Any, *, task_id: str, attempt_id: str) -> str:
    reference = _text(value, "result_ref", maximum=512).replace("\\", "/")
    allowed = {
        f".devfarm/results/{task_id}/result.json",
        f".devfarm/results/{task_id}/attempts/{attempt_id}/result.json",
    }
    if reference not in allowed:
        raise UsefulWorkReportError(
            "result_ref must name the task aggregate or exact attempt result under .devfarm/results"
        )
    return reference


def _safe_paths(value: Any, name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise UsefulWorkReportError(f"{name} must be a sequence")
    paths: list[str] = []
    for item in value:
        path = _text(item, f"{name}[]", maximum=512).replace("\\", "/")
        if path.startswith(("/", "./")) or ":" in path or ".." in path.split("/"):
            raise UsefulWorkReportError(f"{name} contains an unsafe path")
        if path not in paths:
            paths.append(path)
    return tuple(paths)


def _nonnegative(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise UsefulWorkReportError(f"{name} must be a non-negative integer or null")
    return value


def _load_observation(root: Path, raw: Mapping[str, Any], index: int) -> dict[str, Any]:
    allowed = {
        "task_id",
        "attempt_id",
        "result_ref",
        "task_class",
        "source_revision",
        "logical_task_key",
        "baseline_match_ref",
        "review_ref",
        "integration_ref",
        "next_selection_ref",
        "restart_evidence_ref",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise UsefulWorkReportError(f"observations[{index}] has unknown field: {sorted(unknown)[0]}")
    task_id = _safe_id(raw.get("task_id"), f"observations[{index}].task_id")
    attempt_id = _safe_id(raw.get("attempt_id"), f"observations[{index}].attempt_id")
    result_ref = _result_ref(raw.get("result_ref"), task_id=task_id, attempt_id=attempt_id)
    task_class = _text(raw.get("task_class"), f"observations[{index}].task_class", maximum=80)
    if task_class not in _TASK_CLASSES:
        raise UsefulWorkReportError(f"observations[{index}].task_class is unsupported")
    source_revision = _revision(raw.get("source_revision"), f"observations[{index}].source_revision")
    logical_task_key = _safe_id(
        raw.get("logical_task_key", task_id), f"observations[{index}].logical_task_key"
    )
    for field in (
        "baseline_match_ref",
        "review_ref",
        "integration_ref",
        "next_selection_ref",
        "restart_evidence_ref",
    ):
        value = raw.get(field)
        if value is not None:
            _text(value, f"observations[{index}].{field}", maximum=512)

    try:
        result = read_bounded_json(
            root,
            repository_path(root, result_ref, required_parent=".devfarm/results"),
            maximum_chars=250_000,
        )
    except (DevFarmError, OSError) as exc:
        raise UsefulWorkReportError(f"observations[{index}] result artifact is unavailable") from exc
    if not isinstance(result, Mapping) or result.get("schema_version") != 1:
        raise UsefulWorkReportError(f"observations[{index}] result artifact schema is unsupported")
    if result.get("status") != "completed" or result.get("base_revision") != source_revision:
        raise UsefulWorkReportError(f"observations[{index}] result is not completed at the declared revision")
    changed_files = _safe_paths(result.get("changed_files"), f"observations[{index}].changed_files")
    if not changed_files:
        raise UsefulWorkReportError(f"observations[{index}] must contain changed_files")
    if result.get("attempt_id") not in {None, attempt_id}:
        raise UsefulWorkReportError(f"observations[{index}] result attempt_id does not match selection")
    metrics = result.get("worker_metrics")
    if not isinstance(metrics, Mapping):
        raise UsefulWorkReportError(f"observations[{index}] has no worker_metrics")
    required_true = (
        "host_verified",
        "host_tests_passed",
        "independent_verification",
        "durable_recorded",
        "result_accepted",
    )
    missing = [field for field in required_true if metrics.get(field) is not True]
    verified_count = _nonnegative(metrics.get("host_verified_test_count"), f"observations[{index}].host_verified_test_count")
    if missing or not verified_count:
        raise UsefulWorkReportError(
            f"observations[{index}] is not an independently Host-verified accepted result"
        )
    usage = metrics.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    return {
        "task_id": task_id,
        "attempt_id": attempt_id,
        "result_ref": result_ref,
        "task_class": task_class,
        "source_revision": source_revision,
        "logical_task_key": logical_task_key,
        "provider_id": _text(metrics.get("provider_id"), f"observations[{index}].provider_id", maximum=128),
        "provider_binding_id": _text(
            metrics.get("provider_binding_id"), f"observations[{index}].provider_binding_id", maximum=256
        ),
        "model_id": _text(metrics.get("model_id"), f"observations[{index}].model_id", maximum=256),
        "changed_files": list(changed_files),
        "host_verified": True,
        "host_verified_test_count": verified_count,
        "result_accepted": True,
        "review_observed": raw.get("review_ref") is not None,
        "integration_observed": raw.get("integration_ref") is not None,
        "baseline_match_observed": raw.get("baseline_match_ref") is not None,
        "next_selection_observed": raw.get("next_selection_ref") is not None,
        "restart_observed": raw.get("restart_evidence_ref") is not None,
        "prompt_tokens": _nonnegative(usage.get("prompt_tokens"), f"observations[{index}].prompt_tokens"),
        "completion_tokens": _nonnegative(usage.get("completion_tokens"), f"observations[{index}].completion_tokens"),
        "cost_minor": _nonnegative(usage.get("cost_minor"), f"observations[{index}].cost_minor"),
        "elapsed_ms": _nonnegative(metrics.get("elapsed_ms"), f"observations[{index}].elapsed_ms"),
    }


def build_report(root: str | Path, selection: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(selection, Mapping):
        raise UsefulWorkReportError("selection must be an object")
    allowed = {"schema_version", "report_id", "evidence_class", "observations"}
    unknown = set(selection) - allowed
    if unknown:
        raise UsefulWorkReportError(f"selection has unknown field: {sorted(unknown)[0]}")
    if selection.get("schema_version") != 1:
        raise UsefulWorkReportError("selection schema_version must be 1")
    report_id = _safe_id(selection.get("report_id"), "report_id")
    evidence_class = selection.get("evidence_class")
    if evidence_class not in _EVIDENCE_CLASSES:
        raise UsefulWorkReportError("evidence_class is unsupported")
    raw_observations = selection.get("observations")
    if isinstance(raw_observations, (str, bytes)) or not isinstance(raw_observations, Sequence) or not raw_observations:
        raise UsefulWorkReportError("observations must be a non-empty sequence")
    if len(raw_observations) > _MAX_OBSERVATIONS:
        raise UsefulWorkReportError("observations contains too many items")
    root_path = Path(root).resolve()
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_observations):
        if not isinstance(raw, Mapping):
            raise UsefulWorkReportError(f"observations[{index}] must be an object")
        row = _load_observation(root_path, raw, index)
        row["evidence_class"] = evidence_class
        identity = (row["task_id"], row["attempt_id"])
        if identity in seen:
            raise UsefulWorkReportError("observations must not contain duplicate task/attempt identities")
        seen.add(identity)
        rows.append(row)

    distinct_tasks = len({row["logical_task_key"] for row in rows})
    distinct_revisions = len({row["source_revision"] for row in rows})
    summary = {
        "observations_total": len(rows),
        "host_verified": sum(1 for row in rows if row["host_verified"]),
        "result_accepted": sum(1 for row in rows if row["result_accepted"]),
        "review_observed": sum(1 for row in rows if row["review_observed"]),
        "integration_observed": sum(1 for row in rows if row["integration_observed"]),
        "baseline_match_observed": sum(1 for row in rows if row["baseline_match_observed"]),
        "next_selection_observed": sum(1 for row in rows if row["next_selection_observed"]),
        "restart_observed": sum(1 for row in rows if row["restart_observed"]),
        "distinct_task_units": distinct_tasks,
        "distinct_source_revisions": distinct_revisions,
        "total_prompt_tokens": sum(row["prompt_tokens"] or 0 for row in rows),
        "total_completion_tokens": sum(row["completion_tokens"] or 0 for row in rows),
        "total_cost_minor": sum(row["cost_minor"] or 0 for row in rows),
        "total_elapsed_ms": sum(row["elapsed_ms"] or 0 for row in rows),
    }
    missing: list[str] = []
    if evidence_class != "fresh_runtime":
        missing.append("fresh_runtime_acceptance_missing")
    if summary["baseline_match_observed"] < len(rows):
        missing.append("matched_baseline_missing")
    if summary["review_observed"] < len(rows):
        missing.append("review_evidence_missing")
    if summary["integration_observed"] < len(rows):
        missing.append("integration_evidence_missing")
    if summary["next_selection_observed"] == 0:
        missing.append("next_selection_evidence_missing")
    if summary["restart_observed"] == 0:
        missing.append("restart_evidence_missing")
    if distinct_tasks < 2:
        missing.append("two_distinct_task_units_missing")
    status = "PASS" if not missing else "NOT_VERIFIED"
    return {
        "schema_version": 1,
        "evidence_kind": "devfarm_useful_work_acceptance_report",
        "evidence_status": "observed_host_accepted_artifacts",
        "report_id": report_id,
        "status": status,
        "summary": summary,
        "missing_acceptance_conditions": missing,
        "observations": rows,
        "scope_boundary": (
            "This report is read-only measurement evidence. It does not infer "
            "review/integration from Worker claims, promote a Provider/model, "
            "change routing/admission, replay an UNKNOWN effect, or change a Gate."
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report explicit Host-accepted DevFarm useful-work evidence")
    parser.add_argument("selection", help="repository-local selection JSON")
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--output", help="repository-local output path; stdout when omitted")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    try:
        selection = read_bounded_json(root, args.selection, maximum_chars=250_000)
        report = build_report(root, selection)
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            target = repository_path(root, args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except (DevFarmError, OSError, TypeError, ValueError) as exc:
        print(f"useful-work report failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["UsefulWorkReportError", "build_report", "main"]
