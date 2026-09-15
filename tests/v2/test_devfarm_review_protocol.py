from scripts.devfarm_review_protocol import normalize_review_decision, normalize_review_packet
from scripts.devfarm_supervisor_protocol import (
    normalize_review_decision as compatibility_decision,
    normalize_review_packet as compatibility_packet,
)


def test_review_contracts_have_a_neutral_public_boundary() -> None:
    assert compatibility_packet is normalize_review_packet
    assert compatibility_decision is normalize_review_decision

    packet = normalize_review_packet(
        {
            "task_id": "task-1",
            "attempt_id": "attempt-1",
            "status": "HOST_VERIFIED",
            "changed_files": ["src/example.py"],
            "verification_summary": {"status": "PASS"},
            "artifact_refs": [{"path": ".devfarm/results/task-1/result.json"}],
        }
    )
    assert packet["task_id"] == "task-1"

    decision = normalize_review_decision(
        {
            "decision_id": "decision-1",
            "task_id": "task-1",
            "attempt_id": "attempt-1",
            "decision": "APPROVE_INTEGRATION",
            "findings": [],
            "evidence_refs": [{"path": packet["artifact_refs"][0]["path"]}],
        }
    )
    assert decision["decision"] == "APPROVE_INTEGRATION"
