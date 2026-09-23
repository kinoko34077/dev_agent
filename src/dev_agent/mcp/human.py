"""Minimal bounded JSON-lines transport for Codex Human/Expert roles."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Any

from ..human import HumanInteractionPort, HumanRequest, HumanResponse
from ..security.audit import AuditRecorder


_MAX_LINE_BYTES = 16 * 1024
_TOKEN_MAX = 128


class CodexMcpProtocolError(ValueError):
    """Raised when a bounded Codex envelope violates its role contract."""


def _token(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CodexMcpProtocolError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > _TOKEN_MAX or any(ord(char) < 32 or ord(char) == 127 for char in normalized):
        raise CodexMcpProtocolError(f"{name} is not bounded")
    return normalized


@dataclass(frozen=True)
class CodexMcpEnvelope:
    request_id: str
    channel: str
    authority: str
    kind: str
    actor: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        for value, name in (
            (self.request_id, "request_id"),
            (self.channel, "channel"),
            (self.authority, "authority"),
            (self.kind, "kind"),
            (self.actor, "actor"),
        ):
            _token(value, name)
        if self.channel not in {"human_proxy", "expert_assist"}:
            raise CodexMcpProtocolError("unsupported Codex MCP channel")
        if self.authority not in {"HUMAN_REQUIRED", "PROPOSAL_ONLY"}:
            raise CodexMcpProtocolError("unsupported Codex MCP authority")
        if not isinstance(self.payload, Mapping):
            raise CodexMcpProtocolError("payload must be an object")
        safe = AuditRecorder.sanitize_payload(dict(self.payload))
        encoded = json.dumps(self.to_dict_without_payload(safe), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > _MAX_LINE_BYTES:
            raise CodexMcpProtocolError("Codex MCP envelope exceeds the bounded line size")
        object.__setattr__(self, "payload", safe)

    def to_dict_without_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "channel": self.channel,
            "authority": self.authority,
            "kind": self.kind,
            "actor": self.actor,
            "payload": dict(payload),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.to_dict_without_payload(self.payload)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CodexMcpEnvelope":
        if not isinstance(value, Mapping):
            raise CodexMcpProtocolError("Codex MCP envelope must be an object")
        allowed = {"request_id", "channel", "authority", "kind", "actor", "payload"}
        unknown = set(value) - allowed
        if unknown:
            raise CodexMcpProtocolError(f"unknown Codex MCP envelope field: {sorted(unknown)[0]}")
        try:
            return cls(**dict(value))
        except TypeError as exc:
            raise CodexMcpProtocolError(f"invalid Codex MCP envelope: {exc}") from exc


class McpJsonLineTransport:
    """Bounded JSON-lines client using caller-owned send/poll primitives."""

    def __init__(self, *, send_line: Callable[[str], None], poll_line: Callable[[str], str | None]) -> None:
        if not callable(send_line) or not callable(poll_line):
            raise TypeError("send_line and poll_line must be callable")
        self._send_line = send_line
        self._poll_line = poll_line

    def send(self, envelope: CodexMcpEnvelope) -> None:
        if not isinstance(envelope, CodexMcpEnvelope):
            raise TypeError("envelope must be CodexMcpEnvelope")
        encoded = json.dumps(envelope.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > _MAX_LINE_BYTES:
            raise CodexMcpProtocolError("Codex MCP line exceeds the bounded size")
        self._send_line(encoded + "\n")

    def poll(self, request_id: str) -> CodexMcpEnvelope | None:
        request_id = _token(request_id, "request_id")
        line = self._poll_line(request_id)
        if line is None:
            return None
        if not isinstance(line, str):
            raise CodexMcpProtocolError("Codex MCP transport returned a non-text line")
        if len(line.encode("utf-8")) > _MAX_LINE_BYTES:
            raise CodexMcpProtocolError("Codex MCP response exceeds the bounded line size")
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodexMcpProtocolError("Codex MCP response is not JSON") from exc
        envelope = CodexMcpEnvelope.from_dict(value)
        if envelope.request_id != request_id:
            raise CodexMcpProtocolError("Codex MCP response request correlation mismatch")
        return envelope


class CodexMcpHumanAdapter:
    """Human Proxy adapter; Codex output is never treated as Human authority."""

    def __init__(self, port: HumanInteractionPort, transport: McpJsonLineTransport) -> None:
        self._port = port
        self._transport = transport

    def request_human(self, request: HumanRequest) -> None:
        self._port.request_human(request)
        self._transport.send(
            CodexMcpEnvelope(
                request_id=request.request_id,
                channel="human_proxy",
                authority="HUMAN_REQUIRED",
                kind="human_request",
                actor="dev_agent",
                payload=request.to_dict(),
            )
        )

    def poll_response(self, request_id: str) -> HumanResponse | None:
        existing = self._port.poll_response(request_id)
        if existing is not None:
            return existing
        envelope = self._transport.poll(request_id)
        if envelope is None:
            return None
        if envelope.channel != "human_proxy" or envelope.authority != "HUMAN_REQUIRED" or envelope.kind != "human_response":
            raise CodexMcpProtocolError("invalid Human Proxy response role")
        if envelope.actor != "human":
            raise CodexMcpProtocolError("Codex MCP response is not an explicit human actor")
        response = HumanResponse.from_dict(envelope.payload)
        if response.request_id != request_id:
            raise CodexMcpProtocolError("Human response request correlation mismatch")
        self._port.record_response(response)
        return response

    def consume_response(self, request_id: str) -> HumanResponse:
        return self._port.consume_response(request_id)


@dataclass(frozen=True)
class CodexExpertProposal:
    request_id: str
    payload: Mapping[str, Any]
    authority: str = "PROPOSAL_ONLY"

    def __post_init__(self) -> None:
        _token(self.request_id, "request_id")
        if self.authority != "PROPOSAL_ONLY":
            raise CodexMcpProtocolError("Expert Assist proposals must be PROPOSAL_ONLY")
        if not isinstance(self.payload, Mapping):
            raise CodexMcpProtocolError("Expert proposal payload must be an object")
        object.__setattr__(self, "payload", AuditRecorder.sanitize_payload(dict(self.payload)))


class CodexMcpExpertAdapter:
    """Proposal-only Codex Expert Assist adapter."""

    def __init__(self, transport: McpJsonLineTransport) -> None:
        self._transport = transport

    def request(self, request_id: str, *, context: Mapping[str, Any]) -> CodexExpertProposal | None:
        request_id = _token(request_id, "request_id")
        if not isinstance(context, Mapping):
            raise TypeError("context must be an object")
        self._transport.send(
            CodexMcpEnvelope(
                request_id=request_id,
                channel="expert_assist",
                authority="PROPOSAL_ONLY",
                kind="expert_request",
                actor="dev_agent",
                payload=AuditRecorder.sanitize_payload(dict(context)),
            )
        )
        envelope = self._transport.poll(request_id)
        if envelope is None:
            return None
        if envelope.channel != "expert_assist" or envelope.authority != "PROPOSAL_ONLY" or envelope.kind != "proposal":
            raise CodexMcpProtocolError("Expert Assist response must remain PROPOSAL_ONLY")
        if envelope.actor != "codex":
            raise CodexMcpProtocolError("Expert Assist response actor is invalid")
        return CodexExpertProposal(request_id=request_id, payload=envelope.payload)


__all__ = [
    "CodexExpertProposal",
    "CodexMcpEnvelope",
    "CodexMcpExpertAdapter",
    "CodexMcpHumanAdapter",
    "CodexMcpProtocolError",
    "McpJsonLineTransport",
]
