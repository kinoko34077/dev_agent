"""Deterministic validation boundary for future Guardian control handling.

This module evaluates durable ``ControlRequest`` intent against the latest
peer generations.  It deliberately performs no process creation, signalling,
termination, restart, update, or rollback.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from typing import Any, Protocol

from .protocol import (
    ControlAction,
    ControlRequest,
    CoordinationConflict,
    CoordinationValidationError,
    GuardianActionRecord,
    GuardianActionStatus,
    PeerRecord,
    PeerStatus,
)
from .protocol_helpers import timestamp_is_after, validate_identifier, validate_timestamp
from .store import CoordinationStore


class GuardianDecision(str, Enum):
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"
    SENDER_NOT_CURRENT = "SENDER_NOT_CURRENT"
    STALE_REQUEST = "STALE_REQUEST"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_NOT_CURRENT = "TARGET_NOT_CURRENT"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    ACTION_NOT_ALLOWED = "ACTION_NOT_ALLOWED"
    PROFILE_NOT_FOUND = "PROFILE_NOT_FOUND"
    REVISION_MISMATCH = "REVISION_MISMATCH"


@dataclass(frozen=True)
class GuardianEvaluation:
    """Bounded result of Guardian policy validation, not an OS command."""

    request_id: str
    decision: GuardianDecision
    reason: str
    target_role: str
    target_generation: int
    process_action: None = None

    @property
    def accepted(self) -> bool:
        return self.decision is GuardianDecision.ACCEPTED

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "decision": self.decision.value,
            "reason": self.reason,
            "target_role": self.target_role,
            "target_generation": self.target_generation,
            "process_action": None,
        }


_ACTIVE_SENDER_STATUSES = frozenset(
    {
        PeerStatus.STARTING,
        PeerStatus.READY,
        PeerStatus.DRAINING,
        PeerStatus.RESTARTING,
        PeerStatus.STOPPING,
    }
)


class GuardianPolicy:
    """Check sender/target generation fencing without executing requests."""

    def __init__(
        self,
        *,
        allowed_sender_roles: Sequence[str] = ("agent", "codex"),
        allowed_actions: Sequence[ControlAction | str] = tuple(ControlAction),
    ) -> None:
        if isinstance(allowed_sender_roles, (str, bytes)) or not isinstance(allowed_sender_roles, Sequence):
            raise CoordinationValidationError("allowed_sender_roles must be a sequence")
        normalized_roles = tuple(validate_identifier(role, "allowed sender role") for role in allowed_sender_roles)
        if not normalized_roles:
            raise CoordinationValidationError("allowed_sender_roles must not be empty")
        normalized_actions: set[ControlAction] = set()
        for action in allowed_actions:
            try:
                normalized_actions.add(action if isinstance(action, ControlAction) else ControlAction(action))
            except (TypeError, ValueError) as exc:
                raise CoordinationValidationError("allowed action is unsupported") from exc
        if not normalized_actions:
            raise CoordinationValidationError("allowed_actions must not be empty")
        self.allowed_sender_roles = frozenset(normalized_roles)
        self.allowed_actions = frozenset(normalized_actions)

    def evaluate(
        self,
        request: ControlRequest,
        *,
        peers: Sequence[PeerRecord],
        now: str,
    ) -> GuardianEvaluation:
        if not isinstance(request, ControlRequest):
            raise CoordinationValidationError("request must be a ControlRequest")
        if isinstance(peers, (str, bytes)) or not isinstance(peers, Sequence):
            raise CoordinationValidationError("peers must be a sequence")
        for peer in peers:
            if not isinstance(peer, PeerRecord):
                raise CoordinationValidationError("peers must contain PeerRecord values")
        validate_timestamp(now, "now")
        if request.expires_at is not None and request.expires_at <= now:
            return self._result(request, GuardianDecision.EXPIRED, "control request has expired")
        if request.sender_role not in self.allowed_sender_roles:
            return self._result(request, GuardianDecision.SENDER_NOT_CURRENT, "sender role is not admitted")
        latest = self._latest_peers(peers)
        sender = latest.get((request.sender_role, request.sender_instance_id))
        if (
            sender is None
            or sender.generation != request.sender_generation
            or sender.status not in _ACTIVE_SENDER_STATUSES
            or not timestamp_is_after(sender.lease_until, now)
        ):
            return self._result(request, GuardianDecision.SENDER_NOT_CURRENT, "sender generation is not current")
        if request.action not in self.allowed_actions:
            return self._result(request, GuardianDecision.ACTION_NOT_ALLOWED, "control action is not admitted")

        targets = [
            peer
            for (role, _instance_id), peer in latest.items()
            if role == request.target_role
        ]
        if not targets:
            return self._result(request, GuardianDecision.TARGET_NOT_FOUND, "target role is not present")
        newer = [peer for peer in targets if peer.generation > request.target_generation]
        if newer:
            return self._result(request, GuardianDecision.STALE_REQUEST, "target generation is stale")
        matching = [peer for peer in targets if peer.generation == request.target_generation]
        if not matching:
            return self._result(request, GuardianDecision.TARGET_NOT_FOUND, "target generation is not present")
        if len(matching) != 1:
            return self._result(request, GuardianDecision.TARGET_AMBIGUOUS, "target generation is ambiguous")
        if not timestamp_is_after(matching[0].lease_until, now) or matching[0].status not in _ACTIVE_SENDER_STATUSES:
            return self._result(request, GuardianDecision.TARGET_NOT_CURRENT, "target peer lease is not current")
        return self._result(request, GuardianDecision.ACCEPTED, "request is valid for Guardian handling")

    @staticmethod
    def _latest_peers(peers: Sequence[PeerRecord]) -> dict[tuple[str, str], PeerRecord]:
        latest: dict[tuple[str, str], PeerRecord] = {}
        for peer in peers:
            key = (peer.role, peer.instance_id)
            current = latest.get(key)
            if current is None or peer.generation > current.generation:
                latest[key] = peer
        return latest

    @staticmethod
    def _result(request: ControlRequest, decision: GuardianDecision, reason: str) -> GuardianEvaluation:
        return GuardianEvaluation(
            request_id=request.request_id,
            decision=decision,
            reason=reason,
            target_role=request.target_role,
            target_generation=request.target_generation,
        )


class GuardianExecutor(Protocol):
    """Injected deterministic process adapter; no default process authority."""

    def execute(self, request: ControlRequest) -> None:
        ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _request_digest(request: ControlRequest) -> str:
    encoded = json.dumps(
        request.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class GuardianActionService:
    """Journal and fence Guardian intents without owning process operations."""

    def __init__(
        self,
        store: CoordinationStore,
        *,
        policy: GuardianPolicy | None = None,
        executor: GuardianExecutor | None = None,
    ) -> None:
        if not isinstance(store, CoordinationStore):
            raise CoordinationValidationError("store must be a CoordinationStore")
        if policy is not None and not isinstance(policy, GuardianPolicy):
            raise CoordinationValidationError("policy must be a GuardianPolicy")
        if executor is not None and not callable(getattr(executor, "execute", None)):
            raise CoordinationValidationError("executor must provide execute(request)")
        self.store = store
        self.policy = policy or GuardianPolicy()
        self.executor = executor

    def submit(
        self,
        request: ControlRequest,
        *,
        now: str | None = None,
    ) -> GuardianActionRecord:
        """Record, fence, and optionally execute one intent exactly once.

        An existing EXECUTING or UNKNOWN record is never replayed.  A caller
        must reconcile that external outcome before any future operation.
        """

        if not isinstance(request, ControlRequest):
            raise CoordinationValidationError("request must be a ControlRequest")
        timestamp = now or _now()
        validate_timestamp(timestamp, "now")
        digest = _request_digest(request)
        existing = self.store.get_guardian_action(idempotency_key=request.idempotency_key)
        if existing is not None:
            if existing.request_digest != digest:
                raise CoordinationConflict("Guardian idempotency key was reused for different intent")
            if existing.status is not GuardianActionStatus.PENDING or self.executor is None:
                return existing
            return self._execute(existing, request, timestamp)

        evaluation = self.policy.evaluate(
            request,
            peers=self.store.list_peers(),
            now=timestamp,
        )
        accepted = evaluation.decision is GuardianDecision.ACCEPTED
        record = GuardianActionRecord(
            request_id=request.request_id,
            idempotency_key=request.idempotency_key,
            request_digest=digest,
            sender_role=request.sender_role,
            sender_instance_id=request.sender_instance_id,
            sender_generation=request.sender_generation,
            target_role=request.target_role,
            target_generation=request.target_generation,
            action=request.action,
            status=GuardianActionStatus.PENDING if accepted else GuardianActionStatus.REJECTED,
            decision=evaluation.decision.value,
            decision_reason=evaluation.reason,
            created_at=timestamp,
            updated_at=timestamp,
            desired_revision=request.desired_revision,
        )
        persisted = self.store.create_guardian_action(record)
        if persisted.status is not GuardianActionStatus.PENDING or self.executor is None:
            return persisted
        return self._execute(persisted, request, timestamp)

    def reconcile_interrupted(
        self,
        request_id: str,
        *,
        now: str | None = None,
    ) -> GuardianActionRecord:
        """Close an interrupted external attempt as UNKNOWN without retry."""

        timestamp = now or _now()
        validate_timestamp(timestamp, "now")
        return self.store.transition_guardian_action(
            request_id,
            to_status=GuardianActionStatus.UNKNOWN,
            updated_at=timestamp,
            expected_from={GuardianActionStatus.EXECUTING},
            result_code="external_outcome_unknown",
            reconciliation_required=True,
        )

    def reconcile_all_interrupted(
        self,
        *,
        now: str | None = None,
        limit: int = 64,
    ) -> tuple[GuardianActionRecord, ...]:
        """Bounded restart recovery; it never invokes an executor."""

        timestamp = now or _now()
        validate_timestamp(timestamp, "now")
        records = self.store.list_guardian_actions(
            status=GuardianActionStatus.EXECUTING,
            limit=limit,
        )
        reconciled: list[GuardianActionRecord] = []
        for record in records:
            try:
                reconciled.append(self.reconcile_interrupted(record.request_id, now=timestamp))
            except CoordinationConflict:
                current = self.store.get_guardian_action(request_id=record.request_id)
                if current is not None and current.status is GuardianActionStatus.UNKNOWN:
                    reconciled.append(current)
                else:
                    raise
        return tuple(reconciled)

    def _execute(
        self,
        record: GuardianActionRecord,
        request: ControlRequest,
        timestamp: str,
    ) -> GuardianActionRecord:
        if self.executor is None or record.status is not GuardianActionStatus.PENDING:
            return record
        try:
            executing = self.store.transition_guardian_action(
                record.request_id,
                to_status=GuardianActionStatus.EXECUTING,
                updated_at=timestamp,
                expected_from={GuardianActionStatus.PENDING},
            )
        except CoordinationConflict:
            current = self.store.get_guardian_action(request_id=record.request_id)
            if current is None:
                raise
            return current
        try:
            self.executor.execute(request)
        except Exception:
            return self.store.transition_guardian_action(
                executing.request_id,
                to_status=GuardianActionStatus.UNKNOWN,
                updated_at=_now(),
                expected_from={GuardianActionStatus.EXECUTING},
                result_code="external_outcome_unknown",
                reconciliation_required=True,
            )
        return self.store.transition_guardian_action(
            executing.request_id,
            to_status=GuardianActionStatus.COMPLETED,
            updated_at=_now(),
            expected_from={GuardianActionStatus.EXECUTING},
            result_code="executor_completed",
            reconciliation_required=False,
        )


__all__ = [
    "GuardianActionService",
    "GuardianDecision",
    "GuardianEvaluation",
    "GuardianExecutor",
    "GuardianPolicy",
]
