"""Bounded graceful-drain composition for the existing coordination plane."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Any

from .protocol import ArtifactReference, MailboxMessage, PeerRecord, PeerStatus
from .work import ResumeCapsule

if TYPE_CHECKING:
    from .service import ProcessCoordinationService


class ExternalEffectState(str, Enum):
    """Host-observed state of an external effect during a drain request."""

    NONE = "NONE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    IN_PROGRESS = "IN_PROGRESS"
    UNKNOWN = "UNKNOWN"


class DrainDecision(str, Enum):
    READY_TO_RESTART = "READY_TO_RESTART"
    BLOCKED_EXTERNAL_EFFECT = "BLOCKED_EXTERNAL_EFFECT"


@dataclass(frozen=True)
class DrainResult:
    """Durable checkpoint outcome; no process operation is performed here."""

    peer: PeerRecord
    decision: DrainDecision
    reference: ArtifactReference
    message: MailboxMessage
    external_effect_state: ExternalEffectState
    blocked_by: tuple[str, ...] = ()

    @property
    def ready_to_restart(self) -> bool:
        return self.decision is DrainDecision.READY_TO_RESTART

    def to_dict(self) -> dict[str, Any]:
        return {
            "peer": self.peer.to_dict(),
            "decision": self.decision.value,
            "reference": self.reference.to_dict(),
            "message": self.message.to_dict(),
            "external_effect_state": self.external_effect_state.value,
            "blocked_by": list(self.blocked_by),
            "ready_to_restart": self.ready_to_restart,
        }


def _effect_state(value: ExternalEffectState | str) -> ExternalEffectState:
    try:
        return value if isinstance(value, ExternalEffectState) else ExternalEffectState(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("external_effect_state is unsupported") from exc


def _with_blocker(capsule: ResumeCapsule, blocker: str) -> ResumeCapsule:
    blocked_by = tuple(dict.fromkeys((*capsule.blocked_by, blocker)))
    return replace(capsule, status="DRAIN_BLOCKED", blocked_by=blocked_by)


class GracefulDrainService:
    """Prepare one peer for a Guardian restart at a safe checkpoint.

    This service only coordinates presence and checkpoint artifacts.  It does
    not claim Tasks, cancel external work, or invoke a process runtime.
    """

    _SAFE_EFFECT_STATES = frozenset(
        {
            ExternalEffectState.NONE,
            ExternalEffectState.COMPLETED,
            ExternalEffectState.CANCELLED,
        }
    )

    def __init__(self, coordination: ProcessCoordinationService) -> None:
        self.coordination = coordination

    def prepare(
        self,
        peer: PeerRecord,
        *,
        capsule: ResumeCapsule,
        recipient_role: str,
        idempotency_key: str,
        external_effect_state: ExternalEffectState | str = ExternalEffectState.NONE,
        expires_at: str | None = None,
    ) -> DrainResult:
        current = self.coordination.require_current_peer(peer)
        if current.status not in {PeerStatus.READY, PeerStatus.DRAINING}:
            raise ValueError("peer is not drainable in its current status")
        if not isinstance(capsule, ResumeCapsule):
            raise TypeError("capsule must be a ResumeCapsule")
        effect_state = _effect_state(external_effect_state)
        draining = (
            current
            if current.status is PeerStatus.DRAINING
            else self.coordination.set_peer_status(current, PeerStatus.DRAINING)
        )
        if effect_state in self._SAFE_EFFECT_STATES:
            checkpoint = replace(capsule, status="READY_TO_RESTART")
            decision = DrainDecision.READY_TO_RESTART
            blocked_by: tuple[str, ...] = ()
        else:
            blocker = (
                "external_effect_unknown"
                if effect_state is ExternalEffectState.UNKNOWN
                else "external_effect_in_progress"
            )
            checkpoint = _with_blocker(capsule, blocker)
            decision = DrainDecision.BLOCKED_EXTERNAL_EFFECT
            blocked_by = checkpoint.blocked_by
        reference, message = self.coordination.send_checkpoint(
            draining,
            recipient_role=recipient_role,
            capsule=checkpoint,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
        )
        final_peer = (
            self.coordination.set_peer_status(draining, PeerStatus.RESTARTING)
            if decision is DrainDecision.READY_TO_RESTART
            else draining
        )
        return DrainResult(
            peer=final_peer,
            decision=decision,
            reference=reference,
            message=message,
            external_effect_state=effect_state,
            blocked_by=blocked_by,
        )


__all__ = ["DrainDecision", "DrainResult", "ExternalEffectState", "GracefulDrainService"]
