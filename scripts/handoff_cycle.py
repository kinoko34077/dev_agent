"""Development-only Planner -> Executor -> Reviewer composition.

This module is deliberately a one-cycle adapter, not a scheduler or durable
workflow engine.  It uses the existing DevFarm/Codex attempt boundary and
returns a reviewer handoff to the human authority.  A later finite loop may
call this composition again only after the Control Plane authorizes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from scripts.devfarm import DevFarmError
from scripts.devfarm_codex import run_codex_attempt
from src.dev_agent.backends.protocol import AgentBackend
from src.dev_agent.compression import CompressionService, compress_handoff_payload
from src.dev_agent.handoff import (
    ExecutorRole,
    HandoffEnvelope,
    HandoffKind,
    HandoffRole,
    PlannerRole,
    ReviewerRole,
    validate_handoff,
)


class HandoffCycleError(ValueError):
    """A role returned a handoff outside the one-cycle contract."""


def _require_role_transition(
    value: HandoffEnvelope,
    *,
    source: str,
    target: str,
    kinds: set[str],
) -> HandoffEnvelope:
    envelope = validate_handoff(value)
    if envelope.source_role != source or envelope.target_role != target:
        raise HandoffCycleError(
            f"unexpected handoff roles: {envelope.source_role}->{envelope.target_role}"
        )
    if envelope.kind not in kinds:
        raise HandoffCycleError(f"unexpected handoff kind: {envelope.kind}")
    return envelope


@dataclass(frozen=True)
class DevelopmentCycleResult:
    """Evidence from exactly one cycle; ``stopped`` is always true."""

    planner_handoff: HandoffEnvelope
    executor_handoff: HandoffEnvelope
    reviewer_handoff: HandoffEnvelope
    cycle_count: int = 1
    stopped: bool = True


class OneCycleDevelopmentLoop:
    """Run one bounded handoff cycle and stop at the human boundary."""

    def __init__(
        self,
        planner: PlannerRole,
        executor: ExecutorRole,
        reviewer: ReviewerRole,
        *,
        compression_service: CompressionService | None = None,
        max_uncompressed_chars: int | None = None,
        strict_compression: bool = False,
    ) -> None:
        if compression_service is not None:
            if max_uncompressed_chars is None or max_uncompressed_chars <= 0:
                raise ValueError("max_uncompressed_chars is required with compression_service")
        elif max_uncompressed_chars is not None:
            raise ValueError("max_uncompressed_chars requires compression_service")
        self._planner = planner
        self._executor = executor
        self._reviewer = reviewer
        self._compression_service = compression_service
        self._max_uncompressed_chars = max_uncompressed_chars
        self._strict_compression = strict_compression

    def run(
        self,
        *,
        objective: str,
        instruction: str,
        conditions: tuple[str, ...] = (),
        cautions: tuple[str, ...] = (),
        requirements: tuple[str, ...] = (),
        payload: Any = None,
        repository_reference: Mapping[str, Any] | None = None,
        roadmap_reference: Mapping[str, Any] | None = None,
        previous_evidence: Mapping[str, Any] | None = None,
    ) -> DevelopmentCycleResult:
        references: dict[str, Any] = {}
        if repository_reference is not None:
            references["repository"] = dict(repository_reference)
        if roadmap_reference is not None:
            references["roadmap"] = dict(roadmap_reference)
        if previous_evidence is not None:
            references["previous_evidence"] = dict(previous_evidence)
        request = HandoffEnvelope(
            kind=HandoffKind.ANALYSIS_RESULT.value,
            subject=objective,
            instruction=instruction,
            conditions=conditions,
            cautions=cautions,
            requirements=requirements,
            source_role=HandoffRole.HUMAN.value,
            target_role=HandoffRole.PLANNER.value,
            payload=payload,
            payload_mode="reference" if references and payload is None else "original",
            payload_reference=references or None,
        )
        planned = _require_role_transition(
            self._planner.plan(request),
            source=HandoffRole.PLANNER.value,
            target=HandoffRole.EXECUTOR.value,
            kinds={HandoffKind.IMPLEMENTATION_INSTRUCTION.value},
        )
        if self._compression_service is not None:
            planned = compress_handoff_payload(
                planned,
                self._compression_service,
                max_uncompressed_chars=self._max_uncompressed_chars or 1,
                strict_integrity=self._strict_compression,
            )
        executed = _require_role_transition(
            self._executor.execute(planned),
            source=HandoffRole.EXECUTOR.value,
            target=HandoffRole.REVIEWER.value,
            kinds={HandoffKind.EXECUTION_RESULT.value},
        )
        reviewed = _require_role_transition(
            self._reviewer.review(executed),
            source=HandoffRole.REVIEWER.value,
            target=HandoffRole.HUMAN.value,
            kinds={
                HandoffKind.REVIEW_REQUEST.value,
                HandoffKind.ROADMAP_COMPARISON.value,
                HandoffKind.HUMAN_DECISION_REQUIRED.value,
            },
        )
        return DevelopmentCycleResult(
            planner_handoff=planned,
            executor_handoff=executed,
            reviewer_handoff=reviewed,
        )


class CodexDevFarmExecutor(ExecutorRole):
    """Adapt one implementation handoff to the existing Codex DevFarm path."""

    def __init__(
        self,
        root: str | Path,
        *,
        backend: AgentBackend | None = None,
        trust_level: str = "STATIC_ONLY",
        operator_approved: bool = False,
        max_wait_seconds: float = 600.0,
    ) -> None:
        self._root = Path(root).resolve()
        self._backend = backend
        self._trust_level = trust_level
        self._operator_approved = operator_approved
        self._max_wait_seconds = max_wait_seconds

    def execute(self, request: HandoffEnvelope) -> HandoffEnvelope:
        envelope = _require_role_transition(
            request,
            source=HandoffRole.PLANNER.value,
            target=HandoffRole.EXECUTOR.value,
            kinds={HandoffKind.IMPLEMENTATION_INSTRUCTION.value},
        )
        if not isinstance(envelope.payload, Mapping):
            raise HandoffCycleError("Codex DevFarm execution requires a mapping payload")
        manifest_path = envelope.payload.get("manifest_path")
        if not isinstance(manifest_path, str) or not manifest_path.strip():
            raise HandoffCycleError("Codex DevFarm execution requires manifest_path")
        normalized = manifest_path.replace("\\", "/")
        parsed = PurePosixPath(normalized)
        if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
            raise HandoffCycleError("manifest_path must be a safe relative path")
        if not normalized.startswith(".devfarm/tasks/"):
            raise HandoffCycleError("manifest_path must stay under .devfarm/tasks")
        try:
            attempt = run_codex_attempt(
                self._root,
                self._root / normalized,
                backend=self._backend,
                trust_level=self._trust_level,
                operator_approved=self._operator_approved,
                max_wait_seconds=self._max_wait_seconds,
            )
        except DevFarmError as exc:
            raise HandoffCycleError(str(exc)) from exc
        result = attempt["result"]
        safe_payload = {
            "authority_status": attempt["authority_status"],
            "attempt_id": attempt["attempt_id"],
            "changed_files": list(attempt["changed_files"]),
            "patch_sha256": attempt["patch_sha256"],
            "verification_trust_level": attempt["verification_trust_level"],
            "status": result.get("status"),
            "tests_passed": result.get("tests_passed"),
            "result_accepted": result.get("worker_metrics", {}).get("result_accepted"),
            "known_issues": list(result.get("known_issues", [])),
        }
        return HandoffEnvelope(
            kind=HandoffKind.EXECUTION_RESULT.value,
            subject=envelope.subject,
            instruction="現行repoとHost Verification証跡を確認すること",
            source_role=HandoffRole.EXECUTOR.value,
            target_role=HandoffRole.REVIEWER.value,
            payload=safe_payload,
            original_reference={
                "manifest_path": normalized,
                "attempt_id": attempt["attempt_id"],
            },
        )


__all__ = [
    "CodexDevFarmExecutor",
    "DevelopmentCycleResult",
    "HandoffCycleError",
    "OneCycleDevelopmentLoop",
]
