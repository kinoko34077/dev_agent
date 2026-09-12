"""Small constructors for recurrent human-to-model handoff requests.

These helpers only create typed envelopes.  They do not fetch repositories,
call models, select providers, or make authority decisions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .directive import DEFAULT_AUTHORITY_PRECEDENCE, HandoffDirective
from .protocol import ExternalTextReference, HandoffEnvelope, HandoffKind, HandoffRole, PayloadMode


def _references(**values: Mapping[str, Any] | None) -> dict[str, Any]:
    return {name: dict(value) for name, value in values.items() if value is not None}


def _envelope(
    *,
    kind: str,
    subject: str,
    instruction: str,
    directive: HandoffDirective,
    payload: Any = None,
    references: Mapping[str, Any] | None = None,
    source_role: str = HandoffRole.HUMAN.value,
    target_role: str = HandoffRole.PLANNER.value,
    conditions: Sequence[str] = (),
    cautions: Sequence[str] = (),
    requirements: Sequence[str] = (),
) -> HandoffEnvelope:
    reference = dict(references or {})
    return HandoffEnvelope(
        kind=kind,
        subject=subject,
        instruction=instruction,
        source_role=source_role,
        target_role=target_role,
        conditions=tuple(conditions),
        cautions=tuple(cautions),
        requirements=tuple(requirements),
        directive=directive,
        payload=payload,
        payload_mode=PayloadMode.REFERENCE.value if payload is None and reference else PayloadMode.ORIGINAL.value,
        payload_reference=reference or None,
    )


def current_state_request(
    *,
    repository_reference: Mapping[str, Any],
    subject: str = "現行版の確認",
    instruction: str = "現行repoを実際に確認すること",
    continuation_mode: str = "recheck",
) -> HandoffEnvelope:
    """Request a reference-first inspection of the current repository."""

    return _envelope(
        kind=HandoffKind.CURRENT_STATE_REQUEST.value,
        subject=subject,
        instruction=instruction,
        directive=HandoffDirective(
            payload_semantics="current_state_snapshot",
            source_requirements=("current_repository",),
            authority_source="current_repository",
            authority_precedence=DEFAULT_AUTHORITY_PRECEDENCE,
            continuation_mode=continuation_mode,
        ),
        references=_references(repository=repository_reference),
    )


def current_state_analysis(
    *,
    repository_reference: Mapping[str, Any],
    subject: str = "現行版の分析",
    instruction: str = "現行repoを実際に確認して分析すること",
    focus: Sequence[str] = (),
) -> HandoffEnvelope:
    return _envelope(
        kind=HandoffKind.ANALYSIS_RESULT.value,
        subject=subject,
        instruction=instruction,
        directive=HandoffDirective(
            payload_semantics="current_state_snapshot",
            focus=tuple(focus),
            source_requirements=("current_repository",),
            authority_source="current_repository",
            authority_precedence=DEFAULT_AUTHORITY_PRECEDENCE,
            continuation_mode="recheck",
        ),
        references=_references(repository=repository_reference),
    )


def roadmap_comparison(
    *,
    repository_reference: Mapping[str, Any],
    roadmap_reference: Mapping[str, Any],
    subject: str = "現行版とロードマップの比較",
    instruction: str = "現行repoを再確認し、ロードマップと比較すること",
) -> HandoffEnvelope:
    return _envelope(
        kind=HandoffKind.ROADMAP_COMPARISON.value,
        subject=subject,
        instruction=instruction,
        directive=HandoffDirective(
            payload_semantics="roadmap",
            comparison_targets=("current", "roadmap"),
            comparison_axes=("progress", "implemented_state", "acceptance"),
            source_requirements=("current_repository", "roadmap"),
            authority_source="current_repository",
            authority_precedence=DEFAULT_AUTHORITY_PRECEDENCE,
            continuation_mode="recheck",
        ),
        references=_references(repository=repository_reference, roadmap=roadmap_reference),
    )


def reaudit_after_change(
    *,
    repository_reference: Mapping[str, Any],
    previous_findings_reference: Mapping[str, Any] | None = None,
    subject: str = "修正後の再監査",
    instruction: str = "現行repoを再取得し、前回findingsが閉じたか再監査すること",
) -> HandoffEnvelope:
    return _envelope(
        kind=HandoffKind.AUDIT_RESULT.value,
        subject=subject,
        instruction=instruction,
        directive=HandoffDirective(
            payload_semantics="audit_result",
            comparison_targets=("current", "previous_findings"),
            comparison_axes=("implemented_state", "safety", "acceptance"),
            source_requirements=("current_repository", "previous_findings"),
            authority_source="current_repository",
            authority_precedence=DEFAULT_AUTHORITY_PRECEDENCE,
            continuation_mode="reaudit_after_change",
        ),
        references=_references(repository=repository_reference, previous_findings=previous_findings_reference),
    )


def reanalyze_after_correction(
    *,
    latest_correction: str,
    payload: Any,
    subject: str = "訂正後の再分析",
    instruction: str = "最新訂正を前提に再分析すること",
) -> HandoffEnvelope:
    return _envelope(
        kind=HandoffKind.ANALYSIS_RESULT.value,
        subject=subject,
        instruction=instruction,
        directive=HandoffDirective(
            payload_semantics="analysis_result",
            authority_source="latest_user_correction",
            authority_precedence=DEFAULT_AUTHORITY_PRECEDENCE,
            continuation_mode="reanalyze_after_correction",
            latest_correction=latest_correction,
            prior_interpretation_invalidated=True,
        ),
        payload=payload,
    )


def integration_request(
    *,
    payload: Any,
    subject: str = "既存結果の統合",
    instruction: str = "確定事項を保持し、obsoleteな解釈を除外して統合すること",
) -> HandoffEnvelope:
    return _envelope(
        kind=HandoffKind.ANALYSIS_RESULT.value,
        subject=subject,
        instruction=instruction,
        directive=HandoffDirective(
            payload_semantics="analysis_result",
            authority_source="provided_payload",
            authority_precedence=DEFAULT_AUTHORITY_PRECEDENCE,
            continuation_mode="integrate_previous",
        ),
        payload=payload,
    )


def implementation_instruction(
    *,
    payload: Any,
    subject: str = "実装指示",
    instruction: str = "以下を実装し、回帰後に候補成果を返すこと",
    exclusions: Sequence[str] = (),
    conditions: Sequence[str] = (),
    cautions: Sequence[str] = (),
    requirements: Sequence[str] = (),
) -> HandoffEnvelope:
    return _envelope(
        kind=HandoffKind.IMPLEMENTATION_INSTRUCTION.value,
        subject=subject,
        instruction=instruction,
        source_role=HandoffRole.PLANNER.value,
        target_role=HandoffRole.EXECUTOR.value,
        conditions=conditions,
        cautions=cautions,
        requirements=requirements,
        directive=HandoffDirective(
            payload_semantics="implementation_instruction",
            exclusions=tuple(exclusions),
            authority_source="current_user_instruction",
            authority_precedence=DEFAULT_AUTHORITY_PRECEDENCE,
        ),
        payload=payload,
    )


def critical_adjacent_audit(
    *,
    repository_reference: Mapping[str, Any],
    payload: Any = None,
    subject: str = "周辺重大問題の確認",
    instruction: str = "変更面に直結する未知の重大問題を限定して確認すること",
) -> HandoffEnvelope:
    return _envelope(
        kind=HandoffKind.AUDIT_RESULT.value,
        subject=subject,
        instruction=instruction,
        directive=HandoffDirective(
            payload_semantics="audit_result",
            focus=("変更面に直結する重大問題",),
            source_requirements=("current_repository",),
            authority_source="current_repository",
            authority_precedence=DEFAULT_AUTHORITY_PRECEDENCE,
            continuation_mode="recheck",
        ),
        payload=payload,
        references=_references(repository=repository_reference),
    )


def decision_request(
    *,
    payload: Any,
    subject: str = "判断依頼",
    instruction: str = "比較・評価し、Human Authorityの範囲を越えない候補を選択すること",
) -> HandoffEnvelope:
    return _envelope(
        kind=HandoffKind.HUMAN_DECISION_REQUIRED.value,
        subject=subject,
        instruction=instruction,
        directive=HandoffDirective(
            payload_semantics="analysis_result",
            authority_source="provided_payload",
            authority_precedence=DEFAULT_AUTHORITY_PRECEDENCE,
        ),
        payload=payload,
    )


def sequence_planning_request(
    *,
    payload: Any,
    subject: str = "実装順序の検討",
    instruction: str = "依存関係、安全性、手戻り、実装効果、検証可能性で順序化すること",
) -> HandoffEnvelope:
    return _envelope(
        kind=HandoffKind.ANALYSIS_RESULT.value,
        subject=subject,
        instruction=instruction,
        directive=HandoffDirective(
            payload_semantics="roadmap",
            comparison_axes=("dependencies", "safety", "rework", "implementation_effect", "verifiability"),
            authority_source="provided_payload",
            authority_precedence=DEFAULT_AUTHORITY_PRECEDENCE,
        ),
        payload=payload,
    )


def _reference_mapping(value: Mapping[str, Any] | ExternalTextReference, name: str) -> dict[str, Any]:
    if isinstance(value, ExternalTextReference):
        return value.to_dict()
    if not isinstance(value, Mapping) or not value:
        raise TypeError(f"{name} must be a non-empty reference object")
    if value.get("type") == "external_text":
        return ExternalTextReference.from_dict(value).to_dict()
    return dict(value)


def rework_request(
    *,
    task_reference: Mapping[str, Any],
    failure_evidence_reference: Mapping[str, Any] | ExternalTextReference,
    review_findings_reference: Mapping[str, Any] | ExternalTextReference | None = None,
    required_correction: str,
    exclusions: Sequence[str] = (),
    subject: str = "Worker成果の再作業",
    instruction: str = "元のTaskを再送せず、失敗証拠とレビュー差分だけを確認して修正すること",
) -> HandoffEnvelope:
    """Build a compact, reference-first rework handoff.

    The references are data.  They do not grant authority to the receiving
    role, and this helper does not fetch or execute anything.
    """

    if not isinstance(task_reference, Mapping) or not task_reference:
        raise TypeError("task_reference must be a non-empty object")
    references: dict[str, Any] = {
        "type": "rework",
        "task": dict(task_reference),
        "failure_evidence": _reference_mapping(failure_evidence_reference, "failure_evidence_reference"),
    }
    if review_findings_reference is not None:
        references["review_findings"] = _reference_mapping(review_findings_reference, "review_findings_reference")
    return _envelope(
        kind=HandoffKind.REPAIR_REQUEST.value,
        subject=subject,
        instruction=instruction,
        source_role=HandoffRole.REVIEWER.value,
        target_role=HandoffRole.EXECUTOR.value,
        conditions=("既存のTask scopeとAuthorityを維持する",),
        requirements=(required_correction,),
        directive=HandoffDirective(
            payload_semantics="implementation_instruction",
            exclusions=tuple(exclusions),
            source_requirements=("execution_evidence",),
            # A review-generated correction is evidence for the Executor,
            # not a new Human instruction.  Human precedence remains in the
            # canonical authority order when an actual user decision exists.
            authority_source="review_decision",
            authority_precedence=DEFAULT_AUTHORITY_PRECEDENCE,
            continuation_mode="continue",
        ),
        references=references,
    )


__all__ = [
    "critical_adjacent_audit",
    "current_state_analysis",
    "current_state_request",
    "decision_request",
    "implementation_instruction",
    "integration_request",
    "reanalyze_after_correction",
    "reaudit_after_change",
    "roadmap_comparison",
    "rework_request",
    "sequence_planning_request",
]
