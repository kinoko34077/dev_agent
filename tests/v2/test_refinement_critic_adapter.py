from __future__ import annotations

import json
from uuid import uuid4

import pytest

from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.intelligence.critic_adapter import (
    CRITIC_PROPOSAL_RESPONSE_SCHEMA,
    CriticAdapterError,
    ModelCriticAdapter,
)
from src.dev_agent.intelligence.refinement import RefinementProposal


class _Provider:
    provider_id = "independent-critic-provider"

    def __init__(self, response: ModelResponse):
        self.response = response
        self.requests: list[ModelRequest] = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.response


def _packet() -> dict[str, object]:
    return {
        "task_id": "task-refine-1",
        "attempt_id": "attempt-1",
        "failure_class": "semantic_test",
        "failure_summary": "Host verification rejected the expected behavior.",
        "changed_files": ["src/example.py"],
        "patch_sha256": "a" * 64,
        "evidence_refs": [{"kind": "verification", "path": ".devfarm/verification/attempt-1.json"}],
        "acceptance": ["The helper returns the normalized value."],
    }


def _response(*, task_id: str = "task-refine-1", attempt_id: str = "attempt-1") -> ModelResponse:
    return ModelResponse(
        provider="independent-critic-provider",
        model="independent-l1-critic",
        structured_output={
            "task_id": task_id,
            "attempt_id": attempt_id,
            "findings": [
                {
                    "location": "src/example.py:10",
                    "problem": "The failure path keeps the unnormalized value.",
                    "required_correction": "Normalize the value before returning it.",
                }
            ],
            "evidence_refs": [".devfarm/verification/attempt-1.json"],
        },
    )


def test_critic_adapter_returns_bounded_proposal_without_review_authority():
    provider = _Provider(_response())

    proposal = ModelCriticAdapter(provider).propose(_packet())

    assert isinstance(proposal, RefinementProposal)
    assert proposal.task_id == "task-refine-1"
    assert proposal.attempt_id == "attempt-1"
    assert proposal.findings[0].required_correction.startswith("Normalize")
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.requested_capabilities == ["text"]
    assert request.response_schema == CRITIC_PROPOSAL_RESPONSE_SCHEMA
    assert request.metadata["refinement_mode"] == "critic"
    assert request.metadata["proposal_only"] is True
    assert request.metadata["integration_authority"] == "host_and_codex"
    assert request.metadata["allowed_intelligence_tiers"] == ["L1"]
    assert len(request.metadata["egress_manifest_sha256"]) == 64
    assert request.metadata["egress_manifest_policy"] == "dev-agent-model-request-egress-v1"
    assert "diff --git" not in request.messages[0]["content"]
    assert "conversation" not in request.messages[0]["content"]
    assert "failure_summary" in request.messages[0]["content"]


def test_critic_adapter_accepts_bounded_json_text_and_preserves_host_selected_model():
    provider = _Provider(
        ModelResponse(
            provider="independent-critic-provider",
            model="independent-l1-critic",
            text_segments=["```json\n" + json.dumps(_response().structured_output) + "\n```"],
        )
    )

    proposal = ModelCriticAdapter(provider, max_output_tokens=512).propose(_packet())

    assert proposal.attempt_id == "attempt-1"
    assert provider.requests[0].metadata["model_selection"] == "host_selected"


def test_critic_adapter_rejects_raw_or_unbounded_packet_fields_before_dispatch():
    provider = _Provider(_response())

    with pytest.raises(CriticAdapterError, match="raw output"):
        ModelCriticAdapter(provider).propose({**_packet(), "patch": "diff --git"})
    assert provider.requests == []

    with pytest.raises(CriticAdapterError, match="input limit"):
        ModelCriticAdapter(provider).propose({**_packet(), "failure_summary": "x" * 20_001})
    assert provider.requests == []


def test_critic_adapter_rejects_mismatched_identity_and_authority_fields():
    provider = _Provider(_response(task_id="other-task"))
    with pytest.raises(CriticAdapterError, match="task_id"):
        ModelCriticAdapter(provider).propose(_packet())

    unknown = dict(_response().structured_output)
    unknown["decision"] = "APPROVE_INTEGRATION"
    provider = _Provider(
        ModelResponse(
            provider="independent-critic-provider",
            model="independent-l1-critic",
            structured_output=unknown,
        )
    )
    with pytest.raises(CriticAdapterError, match="unknown refinement proposal field"):
        ModelCriticAdapter(provider).propose(_packet())


def test_critic_adapter_uses_protocol_uuid_without_changing_readable_identity():
    task_id = str(uuid4())
    packet = {**_packet(), "task_id": task_id}
    provider = _Provider(_response(task_id=task_id))

    proposal = ModelCriticAdapter(provider).propose(packet)

    assert proposal.task_id == task_id
    assert provider.requests[0].task_id == task_id
