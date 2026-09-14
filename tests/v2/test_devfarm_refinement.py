from __future__ import annotations

from typing import Any

import pytest

from scripts.devfarm_refinement import (
    RefinementCompositionError,
    build_refinement_packet,
    propose_critic,
)
from src.dev_agent.domain.protocol import ModelRequest, ModelResponse
from src.dev_agent.intelligence.refinement import FailureClass, RefinementProposal


class _Runner:
    def __init__(self, packet: dict[str, Any]) -> None:
        self.packet = packet
        self.calls: list[str] = []

    def review_packet(self, task_id: str) -> dict[str, Any]:
        self.calls.append(task_id)
        return dict(self.packet)


class _Provider:
    provider_id = "l1-critic-provider"

    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.requests: list[ModelRequest] = []

    def request(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.response


def _review_packet() -> dict[str, Any]:
    return {
        "task_id": "production-task-1",
        "attempt_id": "attempt-1",
        "status": "HOST_VERIFIED",
        "changed_files": ["src/dev_agent/coordination/work.py"],
        "patch_sha256": "a" * 64,
        "verification_summary": {
            "host_verified": True,
            "host_tests_passed": False,
        },
        "known_issues": ["the focused verification failed"],
        "acceptance": ["the work item is reconciled"],
        "artifact_refs": [
            {"kind": "verification", "path": ".devfarm/verification/attempt-1.json"},
            {"kind": "result", "path": ".devfarm/results/attempt-1.json"},
        ],
        # These fields must never be copied into the Critic packet.
        "patch": "diff --git a/secret.py b/secret.py",
        "stdout": "credential-like output",
    }


def _response() -> ModelResponse:
    return ModelResponse(
        provider="l1-critic-provider",
        model="l1-critic-model",
        structured_output={
            "task_id": "production-task-1",
            "attempt_id": "attempt-1",
            "findings": [
                {
                    "location": "src/dev_agent/coordination/work.py:10",
                    "problem": "The failure path does not reconcile the work state.",
                    "required_correction": "Persist the reconciliation marker before returning.",
                }
            ],
            "evidence_refs": [".devfarm/verification/attempt-1.json"],
        },
    )


def test_build_refinement_packet_uses_public_review_packet_and_allowlists_evidence():
    runner = _Runner(_review_packet())

    packet = build_refinement_packet(
        runner,
        "production-task-1",
        failure_class=FailureClass.SEMANTIC_TEST,
        failure_summary="Host verification rejected the expected behavior.",
    )

    assert runner.calls == ["production-task-1"]
    assert packet == {
        "task_id": "production-task-1",
        "attempt_id": "attempt-1",
        "failure_class": "semantic_test",
        "failure_summary": "Host verification rejected the expected behavior.",
        "changed_files": ["src/dev_agent/coordination/work.py"],
        "patch_sha256": "a" * 64,
        "evidence_refs": [
            {"kind": "verification", "path": ".devfarm/verification/attempt-1.json"},
            {"kind": "result", "path": ".devfarm/results/attempt-1.json"},
        ],
        "acceptance": ["the work item is reconciled"],
    }
    assert "patch" not in packet
    assert "stdout" not in packet
    assert "verification_summary" not in packet


def test_propose_critic_composes_public_packet_with_host_selected_l1_provider():
    runner = _Runner(_review_packet())
    provider = _Provider(_response())

    proposal = propose_critic(
        runner,
        provider,
        "production-task-1",
        failure_class="semantic_test",
        failure_summary="Host verification rejected the expected behavior.",
    )

    assert isinstance(proposal, RefinementProposal)
    assert proposal.task_id == "production-task-1"
    assert proposal.attempt_id == "attempt-1"
    assert len(provider.requests) == 1
    assert provider.requests[0].metadata["model_selection"] == "host_selected"
    assert provider.requests[0].metadata["allowed_intelligence_tiers"] == ["L1"]


def test_build_refinement_packet_rejects_runner_identity_mismatch_before_provider_use():
    runner = _Runner({**_review_packet(), "task_id": "another-task"})

    with pytest.raises(RefinementCompositionError, match="task_id"):
        build_refinement_packet(
            runner,
            "production-task-1",
            failure_class="format_patch",
            failure_summary="The patch could not be applied.",
        )

