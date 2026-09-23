from __future__ import annotations

import json

import pytest

from src.dev_agent.human import HumanRequest, HumanResponse, SQLiteHumanInteractionPort
from src.dev_agent.mcp.human import (
    CodexMcpExpertAdapter,
    CodexMcpHumanAdapter,
    CodexMcpProtocolError,
    CodexExpertProposal,
    McpJsonLineTransport,
)
from src.dev_agent.state.sqlite_store import SQLiteStateStore


def _request() -> HumanRequest:
    return HumanRequest(
        request_id="human-mcp-1",
        root_id="root-mcp-1",
        task_id="task-mcp-1",
        attempt_id="attempt-mcp-1",
        reason="a protected authority decision is required",
        question="Allow the bounded protected-path operation?",
        context={"path": "spec/v2/GATE_STATUS.json"},
        allowed_answers=("allow", "deny"),
        response_shape={"decision": "allow|deny"},
    )


def _transport():
    sent: list[str] = []
    incoming: dict[str, str] = {}

    def send(line: str) -> None:
        sent.append(line)

    def poll(request_id: str) -> str | None:
        return incoming.pop(request_id, None)

    return McpJsonLineTransport(send_line=send, poll_line=poll), sent, incoming


def test_human_adapter_sends_human_required_and_only_accepts_explicit_human_response(tmp_path):
    transport, sent, incoming = _transport()
    request = _request()
    response = HumanResponse(
        request_id=request.request_id,
        responder="human:operator",
        response={"decision": "deny"},
        decision="deny",
        received_at="2026-09-23T00:00:00+00:00",
    )

    with SQLiteStateStore(tmp_path / "mcp-human.sqlite3") as store:
        adapter = CodexMcpHumanAdapter(SQLiteHumanInteractionPort(store), transport)
        adapter.request_human(request)
        envelope = json.loads(sent[0])
        assert envelope["authority"] == "HUMAN_REQUIRED"
        assert envelope["channel"] == "human_proxy"

        incoming[request.request_id] = json.dumps(
            {
                "request_id": request.request_id,
                "channel": "human_proxy",
                "authority": "HUMAN_REQUIRED",
                "kind": "human_response",
                "actor": "codex",
                "payload": response.to_dict(),
            }
        )
        with pytest.raises(CodexMcpProtocolError, match="human actor"):
            adapter.poll_response(request.request_id)
        assert store.get_human_response(request.request_id) is None

        incoming[request.request_id] = json.dumps(
            {
                "request_id": request.request_id,
                "channel": "human_proxy",
                "authority": "HUMAN_REQUIRED",
                "kind": "human_response",
                "actor": "human",
                "payload": response.to_dict(),
            }
        )
        assert adapter.poll_response(request.request_id) == response


def test_expert_adapter_is_proposal_only_and_never_human_authority():
    transport, sent, incoming = _transport()
    incoming["expert-1"] = json.dumps(
        {
            "request_id": "expert-1",
            "channel": "expert_assist",
            "authority": "PROPOSAL_ONLY",
            "kind": "proposal",
            "actor": "codex",
            "payload": {"repair": "bounded proposal"},
        }
    )

    adapter = CodexMcpExpertAdapter(transport)
    proposal = adapter.request("expert-1", context={"failure_class": "SEMANTIC_TEST"})

    assert isinstance(proposal, CodexExpertProposal)
    assert proposal.authority == "PROPOSAL_ONLY"
    assert proposal.payload == {"repair": "bounded proposal"}
    assert json.loads(sent[0])["authority"] == "PROPOSAL_ONLY"


def test_expert_adapter_rejects_human_authority_response():
    transport, _sent, incoming = _transport()
    incoming["expert-2"] = json.dumps(
        {
            "request_id": "expert-2",
            "channel": "expert_assist",
            "authority": "HUMAN_REQUIRED",
            "kind": "proposal",
            "actor": "codex",
            "payload": {"decision": "allow"},
        }
    )

    with pytest.raises(CodexMcpProtocolError, match="PROPOSAL_ONLY"):
        CodexMcpExpertAdapter(transport).request("expert-2", context={})
