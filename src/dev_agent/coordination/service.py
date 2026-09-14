"""Thin composition layer for peers, mailbox, and immutable handoffs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from .artifacts import CoordinationArtifactStore
from .drain import DrainResult, ExternalEffectState
from .guardian import GuardianActionService, GuardianEvaluation, GuardianExecutor, GuardianPolicy
from .protocol import (
    ArtifactReference,
    ControlAction,
    ControlRequest,
    CoordinationConflict,
    CoordinationValidationError,
    GuardianActionRecord,
    HandoffNote,
    MailboxMessage,
    MailboxStatus,
    MessageKind,
    PeerRecord,
    PeerStatus,
)
from .protocol_helpers import validate_identifier, validate_timestamp, validate_text
from .store import CoordinationStore
from .work import ResumeCapsule


@dataclass(frozen=True)
class CoordinationPaths:
    data_dir: Path
    coordination_root: Path
    database: Path
    artifacts: Path

    @classmethod
    def from_data_dir(cls, data_dir: str | Path | None = None) -> "CoordinationPaths":
        if data_dir is None:
            data_dir = os.environ.get("DEV_AGENT_DATA_DIR") or ".dev_agent"
        root = Path(data_dir)
        coordination_root = root / "coordination"
        return cls(
            data_dir=root,
            coordination_root=coordination_root,
            database=coordination_root / "coordination.sqlite3",
            artifacts=coordination_root / "artifacts",
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease_until(now: str, seconds: int | float) -> str:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or seconds <= 0:
        raise CoordinationValidationError("lease_seconds must be positive")
    parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=float(seconds))).isoformat()


class ProcessCoordinationService:
    """Owns only process coordination; Task and process authorities stay elsewhere."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self.paths = CoordinationPaths.from_data_dir(data_dir)
        self.paths.coordination_root.mkdir(parents=True, exist_ok=True)
        self.store = CoordinationStore(self.paths.database)
        self.artifacts = CoordinationArtifactStore(self.paths.coordination_root)

    def _assert_current(self, peer: PeerRecord) -> PeerRecord:
        if not isinstance(peer, PeerRecord):
            raise CoordinationValidationError("peer must be a PeerRecord")
        current = self.store.get_peer(peer.role, peer.instance_id)
        if current is None:
            raise CoordinationConflict("peer is not attached")
        if current.generation != peer.generation:
            raise CoordinationConflict("peer generation is stale")
        if current.status in {PeerStatus.STOPPED, PeerStatus.DEGRADED}:
            raise CoordinationConflict("peer is not active")
        return current

    def require_current_peer(self, peer: PeerRecord) -> PeerRecord:
        """Expose current-peer validation to coordination sub-services."""

        return self._assert_current(peer)

    def assert_work_claim_allowed(self, peer: PeerRecord) -> PeerRecord:
        """Guard new work claims without owning Task scheduling."""

        current = self._assert_current(peer)
        if current.status is not PeerStatus.READY:
            raise CoordinationConflict("peer is not READY; work claims are paused")
        return current

    def prepare_drain(
        self,
        peer: PeerRecord,
        *,
        capsule: ResumeCapsule,
        recipient_role: str,
        idempotency_key: str,
        external_effect_state: ExternalEffectState | str = ExternalEffectState.NONE,
        expires_at: str | None = None,
    ) -> DrainResult:
        """Persist a safe checkpoint before a Guardian-owned restart."""

        from .drain import GracefulDrainService

        return GracefulDrainService(self).prepare(
            peer,
            capsule=capsule,
            recipient_role=recipient_role,
            idempotency_key=idempotency_key,
            external_effect_state=external_effect_state,
            expires_at=expires_at,
        )

    def attach_peer(
        self,
        role: str,
        *,
        revision: str,
        capabilities: Sequence[str] = (),
        pid: int | None = None,
        instance_id: str | None = None,
        lease_seconds: int | float = 30,
        now: str | None = None,
    ) -> PeerRecord:
        role = validate_identifier(role, "role")
        instance_id = validate_identifier(instance_id or str(uuid4()), "instance_id")
        revision = validate_text(revision, "revision", max_chars=512)
        timestamp = now or _now()
        validate_timestamp(timestamp, "now")
        record = PeerRecord(
            role=role,
            instance_id=instance_id,
            generation=self.store.allocate_generation(role, instance_id),
            pid=os.getpid() if pid is None else pid,
            revision=revision,
            started_at=timestamp,
            heartbeat_at=timestamp,
            lease_until=_lease_until(timestamp, lease_seconds),
            status=PeerStatus.STARTING,
            capabilities=tuple(capabilities),
        )
        return self.store.register_peer(record)

    def set_peer_status(self, peer: PeerRecord, status: PeerStatus | str) -> PeerRecord:
        self._assert_current(peer)
        return self.store.set_peer_status(peer.role, peer.instance_id, peer.generation, status)

    def heartbeat(
        self,
        peer: PeerRecord,
        *,
        lease_seconds: int | float = 30,
        now: str | None = None,
    ) -> PeerRecord:
        self._assert_current(peer)
        timestamp = now or _now()
        validate_timestamp(timestamp, "now")
        return self.store.heartbeat(
            peer.role,
            peer.instance_id,
            peer.generation,
            heartbeat_at=timestamp,
            lease_until=_lease_until(timestamp, lease_seconds),
        )

    def detach_peer(self, peer: PeerRecord) -> PeerRecord:
        self._assert_current(peer)
        return self.store.set_peer_status(peer.role, peer.instance_id, peer.generation, PeerStatus.STOPPED)

    def expire_peer_leases(self, *, now: str | None = None) -> tuple[PeerRecord, ...]:
        return self.store.expire_peer_leases(now=now or _now())

    def send_message(
        self,
        sender: PeerRecord,
        *,
        recipient_role: str,
        kind: MessageKind | str,
        subject: str,
        artifact_refs: Sequence[ArtifactReference | Mapping[str, Any]] = (),
        correlation_id: str | None = None,
        requires_ack: bool = True,
        idempotency_key: str,
        expires_at: str | None = None,
    ) -> MailboxMessage:
        current = self._assert_current(sender)
        recipient_role = validate_identifier(recipient_role, "recipient_role")
        timestamp = _now()
        message = MailboxMessage(
            message_id=f"message-{uuid4().hex}",
            sender_role=current.role,
            sender_instance_id=current.instance_id,
            sender_generation=current.generation,
            recipient_role=recipient_role,
            kind=kind,
            subject=subject,
            artifact_refs=tuple(
                item if isinstance(item, ArtifactReference) else ArtifactReference.from_dict(item)
                for item in artifact_refs
            ),
            correlation_id=correlation_id,
            requires_ack=requires_ack,
            idempotency_key=idempotency_key,
            created_at=timestamp,
            expires_at=expires_at,
        )
        return self.store.enqueue(message)

    def send_handoff(
        self,
        sender: PeerRecord,
        note: HandoffNote,
        *,
        idempotency_key: str,
        expires_at: str | None = None,
    ) -> tuple[ArtifactReference, MailboxMessage]:
        current = self._assert_current(sender)
        if not isinstance(note, HandoffNote):
            raise CoordinationValidationError("note must be a HandoffNote")
        if note.from_role != current.role:
            raise CoordinationConflict("handoff sender role does not match the attached peer")
        reference = self.artifacts.put_handoff(note)
        message = self.send_message(
            current,
            recipient_role=note.to_role,
            kind=MessageKind.HANDOFF,
            subject=f"handoff:{note.task_id}",
            artifact_refs=(reference,),
            correlation_id=note.correlation_id,
            idempotency_key=idempotency_key,
            expires_at=expires_at,
        )
        return reference, message

    def send_checkpoint(
        self,
        sender: PeerRecord,
        *,
        recipient_role: str,
        capsule: ResumeCapsule,
        idempotency_key: str,
        expires_at: str | None = None,
    ) -> tuple[ArtifactReference, MailboxMessage]:
        """Persist a bounded resume capsule and announce it through the mailbox."""

        current = self._assert_current(sender)
        if not isinstance(capsule, ResumeCapsule):
            raise CoordinationValidationError("capsule must be a ResumeCapsule")
        reference = self.artifacts.put_json(
            capsule.to_dict(),
            kind="resume_capsule",
            revision=capsule.checkpoint_revision,
        )
        message = self.send_message(
            current,
            recipient_role=recipient_role,
            kind=MessageKind.CHECKPOINT,
            subject=f"checkpoint:{capsule.work_address}",
            artifact_refs=(reference,),
            idempotency_key=idempotency_key,
            expires_at=expires_at,
        )
        return reference, message

    def send_control_request(
        self,
        sender: PeerRecord,
        *,
        target_role: str,
        target_generation: int,
        action: ControlAction | str,
        reason: str,
        idempotency_key: str,
        desired_revision: str | None = None,
        request_id: str | None = None,
        expires_at: str | None = None,
    ) -> tuple[ArtifactReference, MailboxMessage]:
        """Persist and deliver a generation-fenced control intent.

        The deterministic process owner (Guardian) is the only component that
        may later interpret this intent as a process operation.
        """

        current = self._assert_current(sender)
        stable_request_id = request_id or f"control-{uuid5(NAMESPACE_URL, f'dev-agent-control:{idempotency_key}').hex}"
        request = ControlRequest(
            request_id=stable_request_id,
            sender_role=current.role,
            sender_instance_id=current.instance_id,
            sender_generation=current.generation,
            target_role=target_role,
            target_generation=target_generation,
            action=action,
            reason=reason,
            created_at=_now(),
            idempotency_key=idempotency_key,
            desired_revision=desired_revision,
            expires_at=expires_at,
        )
        reference = self.artifacts.put_json(
            request.to_dict(),
            kind="control_request",
            revision=current.revision,
        )
        message = self.send_message(
            current,
            recipient_role=request.target_role,
            kind=MessageKind.CONTROL_REQUEST,
            subject=f"control:{request.action.value}:{request.target_role}:{request.target_generation}",
            artifact_refs=(reference,),
            correlation_id=request.request_id,
            idempotency_key=request.idempotency_key,
            expires_at=request.expires_at,
        )
        return reference, message

    def claim_messages(
        self,
        consumer: PeerRecord,
        *,
        now: str | None = None,
        limit: int = 10,
        lease_seconds: int | float = 60,
    ) -> list[MailboxMessage]:
        current = self._assert_current(consumer)
        return self.store.claim(
            current.role,
            consumer_instance_id=current.instance_id,
            now=now or _now(),
            limit=limit,
            lease_seconds=lease_seconds,
        )

    def ack_message(
        self,
        consumer: PeerRecord,
        message: MailboxMessage,
        *,
        now: str | None = None,
    ) -> MailboxMessage:
        current = self._assert_current(consumer)
        if message.recipient_role != current.role:
            raise CoordinationConflict("message recipient role does not match the consumer")
        return self.store.ack(message.message_id, consumer_instance_id=current.instance_id, now=now or _now())

    def read_handoff(self, reference: ArtifactReference | Mapping[str, Any]) -> HandoffNote:
        return HandoffNote.from_dict(self.artifacts.read_json(reference))

    def read_resume_capsule(self, reference: ArtifactReference | Mapping[str, Any]) -> ResumeCapsule:
        return ResumeCapsule.from_dict(self.artifacts.read_json(reference))

    def read_control_request(self, reference: ArtifactReference | Mapping[str, Any]) -> ControlRequest:
        return ControlRequest.from_dict(self.artifacts.read_json(reference))

    def evaluate_control_request(
        self,
        request: ControlRequest | Mapping[str, Any],
        *,
        now: str | None = None,
        policy: GuardianPolicy | None = None,
    ) -> GuardianEvaluation:
        """Evaluate a durable control intent against current peer records.

        This is a read-only policy operation.  It does not acknowledge the
        mailbox message or perform the requested process action.
        """

        if not isinstance(request, ControlRequest):
            request = ControlRequest.from_dict(request)
        if policy is not None and not isinstance(policy, GuardianPolicy):
            raise CoordinationValidationError("policy must be a GuardianPolicy")
        selected_policy = policy or GuardianPolicy()
        return selected_policy.evaluate(
            request,
            peers=self.store.list_peers(),
            now=now or _now(),
        )

    def submit_guardian_action(
        self,
        request: ControlRequest,
        *,
        policy: GuardianPolicy | None = None,
        executor: GuardianExecutor | None = None,
        now: str | None = None,
    ) -> GuardianActionRecord:
        """Journal a fenced control request and optionally use an injected adapter.

        The default path only records the intent.  Process creation,
        signalling, restart, and rollback remain outside this service.
        """

        return GuardianActionService(
            self.store,
            policy=policy,
            executor=executor,
        ).submit(request, now=now)

    def reconcile_guardian_action(
        self,
        request_id: str,
        *,
        now: str | None = None,
    ) -> GuardianActionRecord:
        return GuardianActionService(self.store).reconcile_interrupted(request_id, now=now)

    def reconcile_guardian_actions(
        self,
        *,
        now: str | None = None,
        limit: int = 64,
    ) -> tuple[GuardianActionRecord, ...]:
        return GuardianActionService(self.store).reconcile_all_interrupted(now=now, limit=limit)

    def close(self) -> None:
        self.store.close()

    def __enter__(self) -> "ProcessCoordinationService":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()


__all__ = ["CoordinationPaths", "ProcessCoordinationService"]
