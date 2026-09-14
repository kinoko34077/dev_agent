import json
from uuid import uuid4

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.intelligence.reviewer_adapter import (
    ModelReviewAdapter,
    ReviewAdapterError,
    ReviewProposal,
    compare_review_proposal,
)


class _Provider:
    def __init__(self, response: ModelResponse):
        self.response = response
        self.requests: list[ModelRequest] = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.response


def _packet() -> dict:
    return {
        "task_id": str(uuid4()),
        "attempt_id": str(uuid4()),
        "status": "HOST_VERIFIED",
        "provider": "gemini",
        "model": "gemini-3.5-flash-lite",
        "changed_files": ["docs/CODEX_DAILY_DOGFOOD.md"],
        "patch_sha256": "a" * 64,
        "verification_summary": {
            "host_verified": True,
            "host_tests_passed": True,
            "independent_verification": True,
        },
        "known_issues": [],
        "acceptance": ["the bounded change is correct"],
        "artifact_refs": [{"kind": "verification", "path": "evidence.json"}],
    }


def _proposal(packet: dict, *, decision: str = "APPROVE_INTEGRATION") -> dict:
    return {
        "task_id": packet["task_id"],
        "attempt_id": packet["attempt_id"],
        "decision": decision,
        "findings": [],
        "evidence_refs": [{"kind": "verification", "path": "evidence.json"}],
        "required_correction": None,
        "rationale": "The compact evidence is sufficient for the proposed review outcome.",
    }


def test_review_proposal_round_trip_is_proposal_only_and_rejects_unknown_fields():
    packet = _packet()
    value = _proposal(packet)
    proposal = ReviewProposal.from_dict(value)

    assert ReviewProposal.from_dict(proposal.to_dict()).to_dict() == proposal.to_dict()
    assert proposal.decision == "APPROVE_INTEGRATION"
    value["authority"] = "human"
    with pytest.raises(ReviewAdapterError, match="unknown"):
        ReviewProposal.from_dict(value)


def test_model_review_adapter_sends_compact_packet_without_raw_worker_output():
    packet = _packet()
    provider = _Provider(
        ModelResponse(
            provider="reviewer-test",
            model="free-l2-reviewer",
            structured_output=_proposal(packet),
        )
    )

    proposal = ModelReviewAdapter(provider).propose(packet)

    assert proposal.task_id == packet["task_id"]
    assert proposal.attempt_id == packet["attempt_id"]
    assert proposal.decision == "APPROVE_INTEGRATION"
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.requested_capabilities == ["text"]
    assert request.response_schema is not None
    assert request.metadata["review_mode"] == "shadow"
    assert request.metadata["authority"] == "host_validation_required"
    assert request.metadata["integration_authority"] == "codex_and_host"
    assert request.metadata["intelligence_routing"] == "bounded"
    assert request.metadata["allowed_intelligence_tiers"] == ["L2"]
    prompt = request.messages[0]["content"]
    assert "docs/CODEX_DAILY_DOGFOOD.md" in prompt
    assert "patch_sha256" in prompt
    assert "raw Worker conversation" in prompt
    assert "required_correction" in prompt
    assert "patch" not in json.loads(prompt.split("review_packet:\n", 1)[1])


def test_model_review_adapter_accepts_bounded_json_text_and_checks_identity():
    packet = _packet()
    payload = _proposal(packet, decision="REWORK")
    payload["findings"] = ["The review needs one bounded correction."]
    payload["required_correction"] = "Keep the note under the Review section."
    provider = _Provider(
        ModelResponse(
            provider="reviewer-test",
            model="free-l2-reviewer",
            text_segments=[json.dumps(payload)],
        )
    )

    proposal = ModelReviewAdapter(provider).propose(packet)

    assert proposal.decision == "REWORK"
    assert proposal.required_correction == "Keep the note under the Review section."

    mismatched = dict(payload)
    mismatched["task_id"] = str(uuid4())
    mismatch_provider = _Provider(
        ModelResponse(
            provider="reviewer-test",
            model="free-l2-reviewer",
            structured_output=mismatched,
        )
    )
    with pytest.raises(ReviewAdapterError, match="task_id"):
        ModelReviewAdapter(mismatch_provider).propose(packet)


def test_review_adapter_rejects_raw_packet_fields_and_requires_rework_correction():
    packet = _packet()
    with pytest.raises(ReviewAdapterError, match="raw output"):
        ModelReviewAdapter(_Provider(ModelResponse(provider="test", model="model", structured_output={}))).propose(
            {**packet, "patch": "inline patch"}
        )

    invalid = _proposal(packet, decision="REWORK")
    invalid["required_correction"] = None
    with pytest.raises(ReviewAdapterError, match="required_correction"):
        ReviewProposal.from_dict(invalid)


def test_shadow_comparison_records_agreement_and_grounded_evidence_without_authority():
    packet = _packet()
    proposal = ReviewProposal.from_dict(_proposal(packet))

    comparison = compare_review_proposal(proposal, "APPROVE_INTEGRATION", packet)

    assert comparison.agreement is True
    assert comparison.false_approve is False
    assert comparison.false_reject is False
    assert comparison.missed_issue is False
    assert comparison.unnecessary_rework is False
    assert comparison.evidence_quality == "grounded"
    assert comparison.to_dict()["codex_decision"] == "APPROVE_INTEGRATION"


def test_shadow_comparison_classifies_disagreement_without_granting_authority():
    packet = _packet()
    value = _proposal(packet, decision="REWORK")
    value["findings"] = ["The bounded change needs correction."]
    value["required_correction"] = "Keep the change within the accepted file scope."
    proposal = ReviewProposal.from_dict(value)

    comparison = compare_review_proposal(proposal, "APPROVE_INTEGRATION", packet)

    assert comparison.agreement is False
    assert comparison.false_approve is False
    assert comparison.false_reject is False
    assert comparison.missed_issue is False
    assert comparison.unnecessary_rework is True
    assert comparison.evidence_quality == "grounded"
