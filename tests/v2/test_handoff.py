from __future__ import annotations

import pytest

from src.dev_agent.handoff import (
    HandoffEnvelope,
    HandoffKind,
    HandoffRole,
    PayloadMode,
    render_handoff,
    validate_handoff,
)


def test_handoff_round_trips_control_payload_and_roles():
    envelope = HandoffEnvelope(
        kind=HandoffKind.IMPLEMENTATION_INSTRUCTION,
        subject="現行版の修正指示",
        instruction="以下を実装し、回帰後に候補成果を返す",
        conditions=("既存DevFarmを再利用する",),
        cautions=("protected authorityを変更しない",),
        requirements=("Host Verificationを通す",),
        payload="変更対象と受入条件",
        source_role=HandoffRole.PLANNER,
        target_role=HandoffRole.EXECUTOR,
        metadata={"cycle": 1},
    )

    restored = validate_handoff(envelope.to_dict())

    assert restored.handoff_id == envelope.handoff_id
    assert restored.kind == HandoffKind.IMPLEMENTATION_INSTRUCTION.value
    assert restored.source_role == HandoffRole.PLANNER.value
    assert restored.target_role == HandoffRole.EXECUTOR.value
    assert restored.control_payload() == {
        "subject": "現行版の修正指示",
        "instruction": "以下を実装し、回帰後に候補成果を返す",
        "conditions": ["既存DevFarmを再利用する"],
        "cautions": ["protected authorityを変更しない"],
        "requirements": ["Host Verificationを通す"],
        "source_role": "planner",
        "target_role": "executor",
    }
    assert restored.payload_for_compression() == "変更対象と受入条件"


def test_kinotch_renderer_preserves_structured_meaning():
    envelope = HandoffEnvelope(
        kind="audit_result",
        subject="現行版の監査結果",
        instruction="以下を基に修正する",
        conditions=("既存境界を維持する",),
        cautions=("secretを外部へ送らない",),
        payload="A=1\nB=2",
        source_role="reviewer",
        target_role="planner",
    )

    assert render_handoff(envelope) == (
        "以下、現行版の監査結果。\n"
        "以下を基に修正する。\n"
        "ただし、既存境界を維持する。\n"
        "secretを外部へ送らないことに注意すること。\n"
        "┈┈┈┈┈┈┈┈┈┈\n"
        "A=1\nB=2"
    )


def test_reference_first_handoff_requires_reference_and_exposes_no_fake_payload():
    envelope = HandoffEnvelope(
        kind=HandoffKind.CURRENT_STATE_REQUEST,
        subject="現行repoの確認",
        instruction="現物を取得してロードマップと比較する",
        payload=None,
        payload_mode=PayloadMode.REFERENCE,
        payload_reference={
            "repository": "kinoko34077/dev_agent",
            "branch": "v2/bootstrap",
            "commit": "4e309a8",
        },
        source_role=HandoffRole.PLANNER,
        target_role=HandoffRole.REVIEWER,
    )

    assert envelope.payload_for_compression() is None
    assert envelope.to_dict()["payload_reference"]["commit"] == "4e309a8"

    with pytest.raises(ValueError, match="payload_reference"):
        HandoffEnvelope(
            kind="current_state_request",
            subject="現行repo",
            instruction="取得する",
            payload=None,
            payload_mode=PayloadMode.REFERENCE,
            source_role="planner",
            target_role="reviewer",
        )


def test_compression_payload_is_payload_only_not_control():
    envelope = HandoffEnvelope(
        kind="analysis_result",
        subject="CONTROL_SECRET_SUBJECT",
        instruction="CONTROL_SECRET_INSTRUCTION",
        conditions=("CONTROL_SECRET_CONDITION",),
        cautions=("CONTROL_SECRET_CAUTION",),
        payload="payload-only-analysis",
        source_role="planner",
        target_role="executor",
    )

    compression_input = envelope.payload_for_compression()
    assert compression_input == "payload-only-analysis"
    assert "CONTROL_SECRET" not in str(compression_input)


@pytest.mark.parametrize("bad_mode", ["", "arbitrary", None])
def test_handoff_rejects_unknown_payload_mode(bad_mode):
    with pytest.raises((TypeError, ValueError)):
        HandoffEnvelope(
            kind="analysis_result",
            subject="subject",
            instruction="instruction",
            payload="payload",
            payload_mode=bad_mode,
            source_role="planner",
            target_role="executor",
        )
