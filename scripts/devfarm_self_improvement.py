"""Bounded Host composition for the F0-F2 self-improvement contracts.

This utility only turns existing Host observations into proposal-only records.
It does not call a model, create a Commander plan, dispatch a Worker, mutate a
Task, grant approval, or integrate a change.  The Supervisor packet is the
only source used for a task observation, so raw Worker conversation and test
output never enter the durable F0-F2 records.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_artifacts import write_immutable_text
from scripts.devfarm_repository import read_bounded_json
from scripts.devfarm_supervisor import CodexSupervisedCommanderRun
from src.dev_agent.intelligence.self_improvement import (
    ImprovementDiagnosis,
    ImprovementPlanProposal,
    ObservationRecord,
    diagnose_observation,
    propose_improvement,
)


MAX_INPUT_CHARS = 250_000
MAX_OUTPUT_CHARS = 250_000
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
_OUTPUT_ROOT = Path(".devfarm") / "self-improvement"


def _safe_identifier(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value.strip()):
        raise DevFarmError(f"{name} must be a safe identifier")
    return value.strip()


def _output_path(root: Path, value: str | Path) -> Path:
    """Resolve a durable output only below the ignored self-improvement area."""

    repository = root.resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = repository / candidate
    candidate = candidate.resolve()
    output_root = (repository / _OUTPUT_ROOT).resolve()
    try:
        candidate.relative_to(output_root)
    except ValueError as exc:
        raise DevFarmError("output must stay below .devfarm/self-improvement") from exc
    if candidate.suffix.lower() != ".json":
        raise DevFarmError("output must be a JSON artifact")
    return candidate


def _write_record_artifact(root: Path, output: Path, record: Mapping[str, Any]) -> Path:
    """Write one immutable, bounded proposal artifact and return its path."""

    path = _output_path(root, output)
    payload = json.dumps(dict(record), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if len(payload) > MAX_OUTPUT_CHARS:
        raise DevFarmError("self-improvement artifact exceeds the bounded size")
    write_immutable_text(path, payload)
    return path


def _print_artifact(record: Mapping[str, Any], path: Path, root: Path) -> None:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    print(json.dumps({"record": dict(record), "artifact_path": relative}, ensure_ascii=False, indent=2))


def _task_status(task: Mapping[str, Any]) -> str:
    value = str(task.get("status", "UNKNOWN")).upper()
    if value in {"HOST_VERIFIED", "INTEGRATED"}:
        return "OBSERVED"
    if value in {"FAILED", "REJECTED", "BLOCKED", "CANCELLED"}:
        return "FAILED"
    if value in {"PROPOSED", "DISPATCHED", "REWORK", "REVIEWING", "INTEGRATING"}:
        return "DEGRADED"
    return "UNKNOWN"


def _task_observation(root: Path, run_id: str, task_id: str) -> ObservationRecord:
    run_id = _safe_identifier(run_id, "run_id")
    task_id = _safe_identifier(task_id, "task_id")
    runner = CodexSupervisedCommanderRun(root, run_id)
    plan = runner.plan()
    task = next((item for item in plan.get("tasks", []) if item.get("task_id") == task_id), None)
    if not isinstance(task, Mapping):
        raise DevFarmError(f"Commander task does not exist: {task_id}")

    packet: Mapping[str, Any] | None = None
    if isinstance(task.get("last_attempt_id"), str) and task["last_attempt_id"].strip():
        try:
            packet = runner.review_packet(task_id)
        except DevFarmError:
            # An in-flight or failed task may not have a ReviewPacket yet.
            # The observation remains valid with the plan/manifest references.
            packet = None

    supervisor = plan.get("supervisor")
    if not isinstance(supervisor, Mapping):
        supervisor = {}
    supervisor_metrics = supervisor.get("metrics")
    if not isinstance(supervisor_metrics, Mapping):
        supervisor_metrics = {}
    task_status = str(task.get("status", "UNKNOWN")).upper()
    verification_summary = packet.get("verification_summary", {}) if packet else {}
    if not isinstance(verification_summary, Mapping):
        verification_summary = {}

    metrics: dict[str, bool | int | str] = {
        "plan_status": str(plan.get("status", "UNKNOWN")),
        "task_status": task_status,
        "plan_revision": int(plan.get("plan_revision", 0) or 0),
        "attempt_count": int(task.get("attempt_count", 0) or 0),
        "review_packet_available": packet is not None,
        "host_verified": bool(verification_summary.get("host_verified", False)),
        "host_tests_passed": bool(verification_summary.get("host_tests_passed", False)),
    }
    for key in ("worker_dispatch_count", "worker_success_count", "codex_review_request_count"):
        value = supervisor_metrics.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            metrics[key] = value

    evidence_refs: list[Mapping[str, Any]] = [
        {"kind": "commander_plan", "path": f".devfarm/plans/{run_id}.json"},
    ]
    manifest_path = task.get("manifest_path")
    if isinstance(manifest_path, str) and manifest_path.strip():
        evidence_refs.append({"kind": "worker_manifest", "path": manifest_path.strip()})
    if packet is not None:
        for key, kind in (("result_ref", "result"), ("verification_ref", "verification")):
            reference = packet.get(key)
            if isinstance(reference, str) and reference.strip():
                evidence_refs.append({"kind": kind, "path": reference.strip()})

    observation_id = f"observation-{run_id}-{task_id}"
    if len(observation_id) > 128:
        digest = hashlib.sha256(f"{run_id}/{task_id}".encode("utf-8")).hexdigest()[:32]
        observation_id = f"observation-{digest}"

    return ObservationRecord(
        subject=f"Commander task {task_id} Host observation",
        source="devfarm_supervisor",
        observed_at=datetime.now(timezone.utc).isoformat(),
        status=_task_status(task),
        metrics=metrics,
        evidence_refs=tuple(evidence_refs),
        observation_id=observation_id,
    )


def _observation_from_file(root: Path, value: str | Path) -> ObservationRecord:
    loaded = read_bounded_json(root, value, maximum_chars=MAX_INPUT_CHARS)
    if not isinstance(loaded, Mapping):
        raise DevFarmError("observation input must be an object")
    return ObservationRecord.from_dict(loaded)


def _diagnosis_from_file(root: Path, value: str | Path) -> ImprovementDiagnosis:
    loaded = read_bounded_json(root, value, maximum_chars=MAX_INPUT_CHARS)
    if not isinstance(loaded, Mapping):
        raise DevFarmError("diagnosis input must be an object")
    return ImprovementDiagnosis.from_dict(loaded)


def _sequence(value: Sequence[str] | None) -> tuple[str, ...]:
    return tuple(value or ())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="bounded Host F0-F2 self-improvement composition")
    sub = parser.add_subparsers(dest="command", required=True)

    observe = sub.add_parser("observe", help="derive one ObservationRecord from a Supervisor plan")
    observe.add_argument("run_id")
    observe.add_argument("task_id")
    observe.add_argument("--root", type=Path, default=Path.cwd())
    observe.add_argument("--output", type=Path, required=True)

    diagnose = sub.add_parser("diagnose", help="create one evidence-grounded Diagnosis proposal")
    diagnose.add_argument("observation_file", type=Path)
    diagnose.add_argument("--root", type=Path, default=Path.cwd())
    diagnose.add_argument("--category", required=True)
    diagnose.add_argument("--severity", required=True)
    diagnose.add_argument("--confidence", required=True)
    diagnose.add_argument("--cause", action="append", default=[])
    diagnose.add_argument("--focus", action="append", default=[])
    diagnose.add_argument("--output", type=Path, required=True)

    plan = sub.add_parser("plan", help="create one Human-approval-required improvement proposal")
    plan.add_argument("observation_file", type=Path)
    plan.add_argument("diagnosis_file", type=Path)
    plan.add_argument("--root", type=Path, default=Path.cwd())
    plan.add_argument("--objective", required=True)
    plan.add_argument("--step", action="append", default=[])
    plan.add_argument("--acceptance", action="append", default=[])
    plan.add_argument("--exclusion", action="append", default=[])
    plan.add_argument("--risk", required=True)
    plan.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = args.root.resolve()
    try:
        if args.command == "observe":
            record = _task_observation(root, args.run_id, args.task_id)
        elif args.command == "diagnose":
            observation = _observation_from_file(root, args.observation_file)
            record = diagnose_observation(
                observation,
                category=args.category,
                severity=args.severity,
                confidence=args.confidence,
                causes=_sequence(args.cause),
                recommended_focus=_sequence(args.focus),
            )
        else:
            observation = _observation_from_file(root, args.observation_file)
            diagnosis = _diagnosis_from_file(root, args.diagnosis_file)
            record = propose_improvement(
                observations=(observation,),
                diagnoses=(diagnosis,),
                objective=args.objective,
                steps=_sequence(args.step),
                acceptance=_sequence(args.acceptance),
                exclusions=_sequence(args.exclusion),
                risk=args.risk,
            )
        path = _write_record_artifact(root, args.output, record.to_dict())
        _print_artifact(record.to_dict(), path, root)
        return 0
    except (DevFarmError, TypeError, ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc), "error_type": type(exc).__name__}, ensure_ascii=False))
        return 2


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
