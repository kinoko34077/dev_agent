"""Render an explicit, read-only W4 candidate-selection observation."""

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

from scripts.devfarm_candidate_selection import (
    CandidateSelectionError,
    CandidateStage,
    WorkerProposalCandidate,
    select_staged_candidates,
)
from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_repository import read_bounded_json, repository_path


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
_EVIDENCE_CLASSES = frozenset({"fresh_runtime", "historical_replay", "synthetic_fixture"})
_SELECTION_FIELDS = {
    "schema_version",
    "report_id",
    "logical_task_id",
    "source_revision",
    "evidence_class",
    "max_candidates",
    "max_resource_units",
    "candidates",
}
_CANDIDATE_FIELDS = {
    "candidate_id",
    "attempt_id",
    "artifact_ref",
    "source_revision",
    "scope_key",
    "provider_binding_id",
    "model_id",
    "prompt_variant",
    "context_variant",
    "resource_units",
    "stages",
}


def _safe_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID.fullmatch(value.strip()):
        raise CandidateSelectionError(f"{name} must be a safe identifier")
    return value.strip()


def _text(value: Any, name: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise CandidateSelectionError(f"{name} must be bounded non-empty text")
    return value.strip()


def _revision(value: Any, name: str) -> str:
    revision = _text(value, name, maximum=64)
    if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise CandidateSelectionError(f"{name} must be a 40-character commit revision")
    return revision


def _artifact_reference(root: Path, value: Any, name: str) -> tuple[str, Mapping[str, Any]]:
    reference = _text(value, name, maximum=512).replace("\\", "/")
    if not reference.startswith(".devfarm/results/"):
        raise CandidateSelectionError(f"{name} must stay under .devfarm/results")
    target = repository_path(root, reference, required_parent=".devfarm/results")
    artifact = read_bounded_json(root, target, maximum_chars=250_000)
    if not isinstance(artifact, Mapping):
        raise CandidateSelectionError(f"{name} must reference a JSON object")
    return reference, artifact


def _stage(root: Path, value: Any, index: int) -> CandidateStage:
    if not isinstance(value, Mapping):
        raise CandidateSelectionError(f"candidates[].stages[{index}] must be an object")
    allowed = {"stage", "status", "evidence_ref", "reason_code"}
    unknown = set(value) - allowed
    if unknown:
        raise CandidateSelectionError(f"candidate stage has unknown field: {sorted(unknown)[0]}")
    evidence_ref = value.get("evidence_ref")
    if evidence_ref is not None:
        reference, _artifact = _artifact_reference(root, evidence_ref, "stage.evidence_ref")
        evidence_ref = reference
    return CandidateStage(
        stage=_text(value.get("stage"), f"candidates[].stages[{index}].stage", maximum=64),
        status=_text(value.get("status"), f"candidates[].stages[{index}].status", maximum=32),
        evidence_ref=evidence_ref,
        reason_code=value.get("reason_code"),
    )


def _candidate(root: Path, raw: Any, *, logical_task_id: str, source_revision: str) -> tuple[WorkerProposalCandidate, dict[str, str]]:
    if not isinstance(raw, Mapping):
        raise CandidateSelectionError("candidate must be an object")
    unknown = set(raw) - _CANDIDATE_FIELDS
    if unknown:
        raise CandidateSelectionError(f"candidate has unknown field: {sorted(unknown)[0]}")
    candidate_id = _safe_id(raw.get("candidate_id"), "candidate_id")
    attempt_id = _safe_id(raw.get("attempt_id"), "attempt_id")
    artifact_ref, artifact = _artifact_reference(root, raw.get("artifact_ref"), "artifact_ref")
    artifact_attempt = artifact.get("attempt_id")
    if artifact_attempt is not None and artifact_attempt != attempt_id:
        raise CandidateSelectionError("artifact attempt_id does not match candidate attempt_id")
    candidate_revision = _revision(raw.get("source_revision", source_revision), "candidate.source_revision")
    if candidate_revision != source_revision:
        raise CandidateSelectionError("candidate source_revision does not match report source_revision")
    stages_raw = raw.get("stages")
    if isinstance(stages_raw, (str, bytes)) or not isinstance(stages_raw, Sequence) or not stages_raw:
        raise CandidateSelectionError("candidate stages must be a non-empty sequence")
    stages = tuple(_stage(root, item, index) for index, item in enumerate(stages_raw))
    candidate = WorkerProposalCandidate(
        logical_task_id=logical_task_id,
        candidate_id=candidate_id,
        attempt_id=attempt_id,
        artifact_ref=artifact_ref,
        source_revision=source_revision,
        scope_key=_text(raw.get("scope_key"), "scope_key"),
        provider_binding_id=_text(raw.get("provider_binding_id"), "provider_binding_id"),
        model_id=_text(raw.get("model_id"), "model_id"),
        prompt_variant=_text(raw.get("prompt_variant"), "prompt_variant"),
        context_variant=_text(raw.get("context_variant"), "context_variant"),
        stages=stages,
        resource_units=raw.get("resource_units", 1),
    )
    return candidate, {"candidate_id": candidate_id, "artifact_ref": artifact_ref, "artifact_status": "readable_json"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Render a bounded W4 candidate-selection observation from explicit "
            "attempt artifacts. No Provider, plan, routing, or integration mutation occurs."
        )
    )
    parser.add_argument("selection", help="repository-local JSON candidate selection")
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--output", help="repository-local report path; stdout when omitted")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    try:
        selection = read_bounded_json(root, args.selection, maximum_chars=500_000)
        if not isinstance(selection, Mapping):
            raise CandidateSelectionError("candidate selection must be an object")
        unknown = set(selection) - _SELECTION_FIELDS
        if unknown:
            raise CandidateSelectionError(f"candidate selection has unknown field: {sorted(unknown)[0]}")
        if selection.get("schema_version") != 1:
            raise CandidateSelectionError("candidate selection schema_version must be 1")
        report_id = _safe_id(selection.get("report_id"), "report_id")
        logical_task_id = _safe_id(selection.get("logical_task_id"), "logical_task_id")
        source_revision = _revision(selection.get("source_revision"), "source_revision")
        evidence_class = _text(selection.get("evidence_class"), "evidence_class", maximum=64)
        if evidence_class not in _EVIDENCE_CLASSES:
            raise CandidateSelectionError("evidence_class is unsupported")
        raw_candidates = selection.get("candidates")
        if isinstance(raw_candidates, (str, bytes)) or not isinstance(raw_candidates, Sequence) or not raw_candidates:
            raise CandidateSelectionError("candidates must be a non-empty sequence")
        if len(raw_candidates) > 32:
            raise CandidateSelectionError("candidates contains too many entries")
        candidates: list[WorkerProposalCandidate] = []
        artifact_checks: list[dict[str, str]] = []
        for raw in raw_candidates:
            candidate, check = _candidate(root, raw, logical_task_id=logical_task_id, source_revision=source_revision)
            candidates.append(candidate)
            artifact_checks.append(check)
        selection_result = select_staged_candidates(
            candidates,
            logical_task_id=logical_task_id,
            max_candidates=selection.get("max_candidates", 4),
            max_resource_units=selection.get("max_resource_units", 8),
        )
        report = {
            "schema_version": 1,
            "evidence_kind": "weak_model_compensation_candidate_selection",
            "evidence_status": "observed_candidate_selection",
            "report_id": report_id,
            "logical_task_id": logical_task_id,
            "source_revision": source_revision,
            "evidence_class": evidence_class,
            "candidate_count": len(candidates),
            "artifact_checks": artifact_checks,
            "candidates": [candidate.to_dict() for candidate in candidates],
            "selection": selection_result.to_dict(),
            "scope_boundary": (
                "This report records explicit side-effect-free candidate screening only. "
                "It does not claim a useful accepted change, Provider qualification, "
                "routing promotion, or Gate evidence."
            ),
        }
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            target = repository_path(root, args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except (CandidateSelectionError, DevFarmError, OSError) as exc:
        print(f"candidate selection report failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

