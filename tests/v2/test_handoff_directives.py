from __future__ import annotations

import pytest

from src.dev_agent.handoff import (
    DEFAULT_AUTHORITY_PRECEDENCE,
    HandoffDirective,
    HandoffEnvelope,
    PayloadMode,
    current_state_request,
    critical_adjacent_audit,
    current_state_analysis,
    decision_request,
    implementation_instruction,
    integration_request,
    reanalyze_after_correction,
    roadmap_comparison,
    reaudit_after_change,
    sequence_planning_request,
    validate_handoff,
)
from src.dev_agent.handoff.renderer import render_handoff


def _directive(**overrides):
    value = {
        "exclusions": ("Compression APIには接続しない",),
        "focus": ("問題点",),
        "payload_semantics": "audit_result",
        "comparison_targets": ("current", "roadmap"),
        "comparison_axes": ("progress", "safety", "acceptance"),
        "output_contract": {
            "section_detail_policy": {
                "successes": "brief",
                "issues": "detailed",
                "roadmap": "list",
            }
        },
        "source_requirements": ("current_repository", "previous_findings"),
        "authority_source": "current_repository",
        "authority_precedence": (
            "current_user_instruction",
            "latest_user_correction",
            "current_repository",
        ),
        "continuation_mode": "reaudit_after_change",
    }
    value.update(overrides)
    return HandoffDirective(**value)


def test_directive_control_round_trips_without_entering_payload():
    envelope = HandoffEnvelope(
        kind="audit_result",
        subject="現行版の監査結果",
        instruction="現行repoを再確認し、前回findingsおよびロードマップと比較すること",
        conditions=("過去回答だけで修正済みと判断しない",),
        cautions=("protected authorityを変更しない",),
        requirements=("Host Verificationの証跡を確認する",),
        directive=_directive(),
        payload="前回の監査本文",
        source_role="reviewer",
        target_role="planner",
    )

    restored = validate_handoff(envelope.to_dict())

    assert restored.directive == envelope.directive
    assert restored.control_payload()["directive"] == envelope.directive.to_dict()
    assert restored.payload_for_compression() == "前回の監査本文"
    assert "Compression APIには接続しない" not in str(restored.payload_for_compression())


def test_reanalyze_after_correction_requires_explicit_latest_correction():
    envelope = HandoffEnvelope(
        kind="analysis_result",
        subject="訂正後の分析",
        instruction="最新訂正を前提に再分析する",
        directive=HandoffDirective(
            continuation_mode="reanalyze_after_correction",
            authority_source="latest_user_correction",
            latest_correction="ここでいう比較は現行repoとロードマップの比較である",
            prior_interpretation_invalidated=True,
        ),
        payload="旧解釈と訂正文",
        source_role="human",
        target_role="planner",
    )

    assert validate_handoff(envelope.to_dict()).directive.prior_interpretation_invalidated is True

    with pytest.raises(ValueError, match="latest_correction"):
        HandoffDirective(
            continuation_mode="reanalyze_after_correction",
            authority_source="latest_user_correction",
            prior_interpretation_invalidated=True,
        )


@pytest.mark.parametrize("continuation", ["", "repeat_forever", None])
def test_directive_rejects_unknown_continuation_mode(continuation):
    with pytest.raises((TypeError, ValueError)):
        HandoffDirective(continuation_mode=continuation)


def test_directive_rejects_authority_precedence_that_reverses_user_correction():
    with pytest.raises(ValueError, match="canonical source order"):
        HandoffDirective(
            authority_precedence=("current_repository", "latest_user_correction"),
        )


def test_default_authority_precedence_keeps_latest_correction_above_prior_context():
    assert DEFAULT_AUTHORITY_PRECEDENCE[:7] == (
        "current_user_instruction",
        "latest_user_correction",
        "specific_requirement",
        "domain_source",
        "core_source",
        "previous_context",
        "general_default",
    )


def test_kinotch_renderer_renders_control_before_payload_stably():
    envelope = HandoffEnvelope(
        kind="audit_result",
        subject="現行版の監査結果",
        instruction="現行repoを再確認し、前回findingsおよびロードマップと比較すること",
        conditions=("過去回答だけで修正済みと判断しない",),
        cautions=("protected authorityを変更しない",),
        requirements=("Host Verificationの証跡を確認する",),
        directive=HandoffDirective(
            exclusions=("Compression APIには接続しない",),
            output_contract={
                "section_detail_policy": {
                    "issues": "detailed",
                    "successes": "brief",
                }
            },
        ),
        payload="監査本文",
        source_role="reviewer",
        target_role="planner",
    )

    assert render_handoff(envelope) == (
        "以下、現行版の監査結果。\n"
        "現行repoを再確認し、前回findingsおよびロードマップと比較すること。\n"
        "ただし、過去回答だけで修正済みと判断しない。\n"
        "protected authorityを変更しないことに注意すること。\n"
        "Host Verificationの証跡を確認する。\n"
        "Compression APIには接続しない。\n"
        "問題点は詳細、正常部分は簡潔に示すこと。\n"
        "┈┈┈┈┈┈┈┈┈┈\n"
        "監査本文"
    )


def test_reference_first_presets_construct_controls_without_performing_work():
    repository = {"repository": "kinoko34077/dev_agent", "branch": "v2/bootstrap", "commit": "abc1234"}
    roadmap = {"path": "docs/V2_EXECUTION_PLAN.md"}

    current = current_state_request(repository_reference=repository)
    comparison = roadmap_comparison(repository_reference=repository, roadmap_reference=roadmap)
    correction = reanalyze_after_correction(
        latest_correction="Compression Serviceはまだ接続しない",
        payload="前段の分析結果",
    )

    assert current.payload_mode == PayloadMode.REFERENCE.value
    assert current.directive.authority_source == "current_repository"
    assert current.directive.source_requirements == ("current_repository",)
    assert comparison.directive.comparison_targets == ("current", "roadmap")
    assert comparison.directive.comparison_axes == ("progress", "implemented_state", "acceptance")
    assert correction.directive.continuation_mode == "reanalyze_after_correction"
    assert correction.directive.prior_interpretation_invalidated is True


def test_all_recurrent_request_presets_only_construct_handoffs():
    repository = {"repository": "kinoko34077/dev_agent", "branch": "v2/bootstrap", "commit": "abc1234"}
    roadmap = {"path": "docs/V2_EXECUTION_PLAN.md"}
    payload = {"finding": "example"}

    values = (
        current_state_request(repository_reference=repository),
        current_state_analysis(repository_reference=repository),
        roadmap_comparison(repository_reference=repository, roadmap_reference=roadmap),
        reaudit_after_change(repository_reference=repository),
        reanalyze_after_correction(latest_correction="旧解釈は無効", payload=payload),
        integration_request(payload=payload),
        implementation_instruction(payload=payload),
        critical_adjacent_audit(repository_reference=repository),
        decision_request(payload=payload),
        sequence_planning_request(payload=payload),
    )

    assert all(isinstance(value, HandoffEnvelope) for value in values)
    assert all(value.source_role for value in values)
    assert all(value.target_role for value in values)
