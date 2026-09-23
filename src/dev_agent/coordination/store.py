"""Durable SQLite store for process presence and mailbox delivery."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any, Iterator

from .._sqlite import connect
from .protocol import (
    CoordinationConflict,
    CoordinationValidationError,
    GuardianActionRecord,
    GuardianActionStatus,
    MailboxMessage,
    MailboxStatus,
    MessageKind,
    PeerRecord,
    PeerStatus,
)
from .protocol_helpers import validate_identifier, validate_timestamp


_MAX_CLAIM_LIMIT = 64

_GUARDIAN_ACTION_TRANSITIONS = {
    GuardianActionStatus.PENDING: frozenset({GuardianActionStatus.EXECUTING, GuardianActionStatus.UNKNOWN}),
    GuardianActionStatus.EXECUTING: frozenset({GuardianActionStatus.COMPLETED, GuardianActionStatus.UNKNOWN}),
    GuardianActionStatus.COMPLETED: frozenset(),
    GuardianActionStatus.REJECTED: frozenset(),
    GuardianActionStatus.UNKNOWN: frozenset(),
}


def _parse_timestamp(value: str, name: str = "timestamp") -> datetime:
    validate_timestamp(value, name)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CoordinationValidationError(f"{name} must include a timezone")
    return parsed


def _due(value: str | None, now: str) -> bool:
    return value is not None and _parse_timestamp(value, "timestamp") <= _parse_timestamp(now, "now")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _peer_from_row(row: sqlite3.Row) -> PeerRecord:
    return PeerRecord(
        role=row["role"],
        instance_id=row["instance_id"],
        generation=row["generation"],
        pid=row["pid"],
        revision=row["revision"],
        started_at=row["started_at"],
        heartbeat_at=row["heartbeat_at"],
        lease_until=row["lease_until"],
        status=row["status"],
        capabilities=tuple(json.loads(row["capabilities_json"])),
    )


def _message_from_row(row: sqlite3.Row) -> MailboxMessage:
    return MailboxMessage(
        message_id=row["message_id"],
        sender_role=row["sender_role"],
        sender_instance_id=row["sender_instance_id"],
        sender_generation=row["sender_generation"],
        recipient_role=row["recipient_role"],
        kind=row["kind"],
        subject=row["subject"],
        artifact_refs=tuple(json.loads(row["artifact_refs_json"])),
        correlation_id=row["correlation_id"],
        requires_ack=bool(row["requires_ack"]),
        idempotency_key=row["idempotency_key"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        status=row["status"],
        claimed_by=row["claimed_by"],
        claimed_at=row["claimed_at"],
        claim_lease_until=row["claim_lease_until"],
        attempt_count=row["attempt_count"],
    )


def _guardian_action_from_row(row: sqlite3.Row) -> GuardianActionRecord:
    return GuardianActionRecord(
        request_id=row["request_id"],
        idempotency_key=row["idempotency_key"],
        request_digest=row["request_digest"],
        sender_role=row["sender_role"],
        sender_instance_id=row["sender_instance_id"],
        sender_generation=row["sender_generation"],
        target_role=row["target_role"],
        target_generation=row["target_generation"],
        action=row["action"],
        status=row["status"],
        decision=row["decision"],
        decision_reason=row["decision_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        desired_revision=row["desired_revision"],
        result_code=row["result_code"],
        reconciliation_required=bool(row["reconciliation_required"]),
    )


def _guardian_action_identity(record: GuardianActionRecord) -> tuple[Any, ...]:
    return (
        record.request_id,
        record.idempotency_key,
        record.request_digest,
        record.sender_role,
        record.sender_instance_id,
        record.sender_generation,
        record.target_role,
        record.target_generation,
        record.action.value,
        record.decision,
        record.decision_reason,
        record.created_at,
        record.desired_revision,
    )


def _message_identity(message: MailboxMessage) -> tuple[Any, ...]:
    """Return immutable delivery intent, excluding mutable claim state."""

    return (
        message.sender_role,
        message.sender_instance_id,
        message.sender_generation,
        message.recipient_role,
        message.kind.value,
        message.subject,
        tuple(_json(item.to_dict()) for item in message.artifact_refs),
        message.correlation_id,
        message.requires_ack,
        message.idempotency_key,
        message.created_at,
        message.expires_at,
    )


class CoordinationStore:
    """A separate, small SQLite authority for peer and mailbox state."""

    SCHEMA_VERSION = 2

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = connect(path)
        self.connection.row_factory = sqlite3.Row
        self._lock = RLock()
        self._closed = False
        self._create_schema()

    def _create_schema(self) -> None:
        with self._lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS coordination_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS peers (
                    role TEXT NOT NULL,
                    instance_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    pid INTEGER,
                    revision TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    lease_until TEXT NOT NULL,
                    status TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    PRIMARY KEY (role, instance_id, generation)
                );
                CREATE INDEX IF NOT EXISTS peers_instance_idx
                    ON peers(role, instance_id, generation DESC);
                CREATE TABLE IF NOT EXISTS mailbox (
                    message_id TEXT PRIMARY KEY,
                    sender_role TEXT NOT NULL,
                    sender_instance_id TEXT NOT NULL,
                    sender_generation INTEGER NOT NULL,
                    recipient_role TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    correlation_id TEXT,
                    requires_ack INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    status TEXT NOT NULL,
                    claimed_by TEXT,
                    claimed_at TEXT,
                    claim_lease_until TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS guardian_actions (
                    request_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    sender_role TEXT NOT NULL,
                    sender_instance_id TEXT NOT NULL,
                    sender_generation INTEGER NOT NULL,
                    target_role TEXT NOT NULL,
                    target_generation INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    decision_reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    desired_revision TEXT,
                    result_code TEXT,
                    reconciliation_required INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS mailbox_recipient_status_idx
                    ON mailbox(recipient_role, status, created_at, message_id);
                CREATE INDEX IF NOT EXISTS mailbox_claim_lease_idx
                    ON mailbox(status, claim_lease_until);
                CREATE INDEX IF NOT EXISTS guardian_actions_status_idx
                    ON guardian_actions(status, updated_at, request_id);
                """
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO coordination_meta(key, value) VALUES('schema_version', ?)",
                (str(self.SCHEMA_VERSION),),
            )
            current_version = int(
                self.connection.execute(
                    "SELECT value FROM coordination_meta WHERE key='schema_version'"
                ).fetchone()["value"]
            )
            if current_version > self.SCHEMA_VERSION:
                raise CoordinationValidationError("coordination schema version is newer than this runtime")
            if current_version < self.SCHEMA_VERSION:
                self.connection.execute(
                    "UPDATE coordination_meta SET value=? WHERE key='schema_version'",
                    (str(self.SCHEMA_VERSION),),
                )
            self.connection.commit()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self._closed:
                raise RuntimeError("coordination store is closed")
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except Exception:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def allocate_generation(self, role: str, instance_id: str) -> int:
        role = validate_identifier(role, "role")
        instance_id = validate_identifier(instance_id, "instance_id")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(generation), 0) AS generation FROM peers WHERE role=? AND instance_id=?",
                (role, instance_id),
            ).fetchone()
            return int(row["generation"]) + 1

    def register_peer(self, peer: PeerRecord) -> PeerRecord:
        if not isinstance(peer, PeerRecord):
            raise CoordinationValidationError("peer must be a PeerRecord")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM peers WHERE role=? AND instance_id=? AND generation=?",
                (peer.role, peer.instance_id, peer.generation),
            ).fetchone()
            if row is not None:
                existing = _peer_from_row(row)
                if existing != peer:
                    raise CoordinationConflict("peer generation already exists with different identity")
                return existing
            latest = connection.execute(
                "SELECT MAX(generation) AS generation FROM peers WHERE role=? AND instance_id=?",
                (peer.role, peer.instance_id),
            ).fetchone()["generation"]
            if latest is not None and peer.generation < int(latest):
                raise CoordinationConflict("peer generation is older than the current generation")
            connection.execute(
                """INSERT INTO peers(
                    role, instance_id, generation, pid, revision, started_at,
                    heartbeat_at, lease_until, status, capabilities_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    peer.role,
                    peer.instance_id,
                    peer.generation,
                    peer.pid,
                    peer.revision,
                    peer.started_at,
                    peer.heartbeat_at,
                    peer.lease_until,
                    peer.status.value,
                    _json(list(peer.capabilities)),
                ),
            )
            return peer

    def _current_peer_row(
        self,
        connection: sqlite3.Connection,
        role: str,
        instance_id: str,
        generation: int,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM peers WHERE role=? AND instance_id=? AND generation=?",
            (role, instance_id, generation),
        ).fetchone()
        if row is None:
            latest = connection.execute(
                "SELECT MAX(generation) AS generation FROM peers WHERE role=? AND instance_id=?",
                (role, instance_id),
            ).fetchone()["generation"]
            if latest is not None and int(latest) > generation:
                raise CoordinationConflict("peer generation is stale")
            raise CoordinationConflict("peer does not exist")
        latest = connection.execute(
            "SELECT MAX(generation) AS generation FROM peers WHERE role=? AND instance_id=?",
            (role, instance_id),
        ).fetchone()["generation"]
        if latest is not None and int(latest) != generation:
            raise CoordinationConflict("peer generation is stale")
        return row

    def get_peer(self, role: str, instance_id: str, generation: int | None = None) -> PeerRecord | None:
        role = validate_identifier(role, "role")
        instance_id = validate_identifier(instance_id, "instance_id")
        with self._lock:
            if self._closed:
                raise RuntimeError("coordination store is closed")
            if generation is None:
                row = self.connection.execute(
                    "SELECT * FROM peers WHERE role=? AND instance_id=? ORDER BY generation DESC LIMIT 1",
                    (role, instance_id),
                ).fetchone()
            else:
                row = self.connection.execute(
                    "SELECT * FROM peers WHERE role=? AND instance_id=? AND generation=?",
                    (role, instance_id, generation),
                ).fetchone()
            return _peer_from_row(row) if row is not None else None

    def list_peers(self, role: str | None = None) -> tuple[PeerRecord, ...]:
        if role is not None:
            role = validate_identifier(role, "role")
        with self._lock:
            if role is None:
                rows = self.connection.execute(
                    "SELECT * FROM peers ORDER BY role, instance_id, generation"
                ).fetchall()
            else:
                rows = self.connection.execute(
                    "SELECT * FROM peers WHERE role=? ORDER BY instance_id, generation",
                    (role,),
                ).fetchall()
            return tuple(_peer_from_row(row) for row in rows)

    def heartbeat(
        self,
        role: str,
        instance_id: str,
        generation: int,
        *,
        heartbeat_at: str,
        lease_until: str,
    ) -> PeerRecord:
        role = validate_identifier(role, "role")
        instance_id = validate_identifier(instance_id, "instance_id")
        validate_timestamp(heartbeat_at, "heartbeat_at")
        validate_timestamp(lease_until, "lease_until")
        with self._transaction() as connection:
            self._current_peer_row(connection, role, instance_id, generation)
            connection.execute(
                "UPDATE peers SET heartbeat_at=?, lease_until=? WHERE role=? AND instance_id=? AND generation=?",
                (heartbeat_at, lease_until, role, instance_id, generation),
            )
            row = connection.execute(
                "SELECT * FROM peers WHERE role=? AND instance_id=? AND generation=?",
                (role, instance_id, generation),
            ).fetchone()
            return _peer_from_row(row)

    def set_peer_status(
        self,
        role: str,
        instance_id: str,
        generation: int,
        status: PeerStatus | str,
    ) -> PeerRecord:
        role = validate_identifier(role, "role")
        instance_id = validate_identifier(instance_id, "instance_id")
        try:
            normalized = status if isinstance(status, PeerStatus) else PeerStatus(status)
        except (TypeError, ValueError) as exc:
            raise CoordinationValidationError("status has an unsupported value") from exc
        with self._transaction() as connection:
            self._current_peer_row(connection, role, instance_id, generation)
            connection.execute(
                "UPDATE peers SET status=? WHERE role=? AND instance_id=? AND generation=?",
                (normalized.value, role, instance_id, generation),
            )
            row = connection.execute(
                "SELECT * FROM peers WHERE role=? AND instance_id=? AND generation=?",
                (role, instance_id, generation),
            ).fetchone()
            return _peer_from_row(row)

    def expire_peer_leases(self, *, now: str) -> tuple[PeerRecord, ...]:
        validate_timestamp(now, "now")
        changed: list[PeerRecord] = []
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM peers WHERE status NOT IN (?, ?) ORDER BY role, instance_id, generation",
                (PeerStatus.STOPPED.value, PeerStatus.DEGRADED.value),
            ).fetchall()
            for row in rows:
                if _due(row["lease_until"], now):
                    connection.execute(
                        "UPDATE peers SET status=? WHERE role=? AND instance_id=? AND generation=?",
                        (PeerStatus.DEGRADED.value, row["role"], row["instance_id"], row["generation"]),
                    )
                    updated = connection.execute(
                        "SELECT * FROM peers WHERE role=? AND instance_id=? AND generation=?",
                        (row["role"], row["instance_id"], row["generation"]),
                    ).fetchone()
                    changed.append(_peer_from_row(updated))
        return tuple(changed)

    def create_guardian_action(self, record: GuardianActionRecord) -> GuardianActionRecord:
        """Persist one new Guardian journal entry idempotently."""

        if not isinstance(record, GuardianActionRecord):
            raise CoordinationValidationError("record must be a GuardianActionRecord")
        if record.status not in {GuardianActionStatus.PENDING, GuardianActionStatus.REJECTED}:
            raise CoordinationValidationError("new Guardian actions must be pending or rejected")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM guardian_actions WHERE idempotency_key=?",
                (record.idempotency_key,),
            ).fetchone()
            if existing is not None:
                restored = _guardian_action_from_row(existing)
                if _guardian_action_identity(restored) != _guardian_action_identity(record):
                    raise CoordinationConflict("Guardian idempotency key was reused for different intent")
                return restored
            existing_id = connection.execute(
                "SELECT * FROM guardian_actions WHERE request_id=?",
                (record.request_id,),
            ).fetchone()
            if existing_id is not None:
                raise CoordinationConflict("Guardian request id was reused for different intent")
            connection.execute(
                """INSERT INTO guardian_actions(
                    request_id, idempotency_key, request_digest,
                    sender_role, sender_instance_id, sender_generation,
                    target_role, target_generation, action,
                    status, decision, decision_reason,
                    created_at, updated_at, desired_revision,
                    result_code, reconciliation_required
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.request_id,
                    record.idempotency_key,
                    record.request_digest,
                    record.sender_role,
                    record.sender_instance_id,
                    record.sender_generation,
                    record.target_role,
                    record.target_generation,
                    record.action.value,
                    record.status.value,
                    record.decision,
                    record.decision_reason,
                    record.created_at,
                    record.updated_at,
                    record.desired_revision,
                    record.result_code,
                    int(record.reconciliation_required),
                ),
            )
            return record

    def get_guardian_action(
        self,
        *,
        request_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> GuardianActionRecord | None:
        if (request_id is None) == (idempotency_key is None):
            raise CoordinationValidationError("provide exactly one Guardian action lookup key")
        if request_id is not None:
            request_id = validate_identifier(request_id, "request_id")
            query = "SELECT * FROM guardian_actions WHERE request_id=?"
            params = (request_id,)
        else:
            idempotency_key = validate_identifier(idempotency_key, "idempotency_key")
            query = "SELECT * FROM guardian_actions WHERE idempotency_key=?"
            params = (idempotency_key,)
        with self._lock:
            if self._closed:
                raise RuntimeError("coordination store is closed")
            row = self.connection.execute(query, params).fetchone()
            return _guardian_action_from_row(row) if row is not None else None

    def list_guardian_actions(
        self,
        *,
        status: GuardianActionStatus | str | None = None,
        limit: int = _MAX_CLAIM_LIMIT,
    ) -> tuple[GuardianActionRecord, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= _MAX_CLAIM_LIMIT:
            raise CoordinationValidationError(f"limit must be between 1 and {_MAX_CLAIM_LIMIT}")
        normalized_status = None
        if status is not None:
            try:
                normalized_status = status if isinstance(status, GuardianActionStatus) else GuardianActionStatus(status)
            except (TypeError, ValueError) as exc:
                raise CoordinationValidationError("Guardian action status is unsupported") from exc
        with self._lock:
            if self._closed:
                raise RuntimeError("coordination store is closed")
            if normalized_status is None:
                rows = self.connection.execute(
                    "SELECT * FROM guardian_actions ORDER BY updated_at, request_id LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    "SELECT * FROM guardian_actions WHERE status=? ORDER BY updated_at, request_id LIMIT ?",
                    (normalized_status.value, limit),
                ).fetchall()
            return tuple(_guardian_action_from_row(row) for row in rows)

    def transition_guardian_action(
        self,
        request_id: str,
        *,
        to_status: GuardianActionStatus | str,
        updated_at: str,
        expected_from: set[GuardianActionStatus | str] | None = None,
        result_code: str | None = None,
        reconciliation_required: bool | None = None,
    ) -> GuardianActionRecord:
        request_id = validate_identifier(request_id, "request_id")
        validate_timestamp(updated_at, "updated_at")
        try:
            normalized = to_status if isinstance(to_status, GuardianActionStatus) else GuardianActionStatus(to_status)
        except (TypeError, ValueError) as exc:
            raise CoordinationValidationError("Guardian action status is unsupported") from exc
        expected = None
        if expected_from is not None:
            if isinstance(expected_from, (str, bytes)) or not expected_from:
                raise CoordinationValidationError("expected_from must be a non-empty set")
            try:
                expected = {
                    item if isinstance(item, GuardianActionStatus) else GuardianActionStatus(item)
                    for item in expected_from
                }
            except (TypeError, ValueError) as exc:
                raise CoordinationValidationError("expected_from contains an unsupported status") from exc
        if result_code is not None:
            result_code = validate_identifier(result_code, "result_code")
        if reconciliation_required is not None and not isinstance(reconciliation_required, bool):
            raise CoordinationValidationError("reconciliation_required must be a boolean")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM guardian_actions WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise CoordinationConflict("Guardian action does not exist")
            current = _guardian_action_from_row(row)
            if expected is not None and current.status not in expected:
                raise CoordinationConflict("Guardian action status is not the expected state")
            if normalized not in _GUARDIAN_ACTION_TRANSITIONS[current.status]:
                raise CoordinationConflict("Guardian action transition is not allowed")
            updated = replace(
                current,
                status=normalized,
                updated_at=updated_at,
                result_code=result_code if result_code is not None else current.result_code,
                reconciliation_required=(
                    reconciliation_required
                    if reconciliation_required is not None
                    else current.reconciliation_required
                ),
            )
            connection.execute(
                """UPDATE guardian_actions
                   SET status=?, updated_at=?, result_code=?, reconciliation_required=?
                   WHERE request_id=?""",
                (
                    updated.status.value,
                    updated.updated_at,
                    updated.result_code,
                    int(updated.reconciliation_required),
                    updated.request_id,
                ),
            )
            return updated

    def enqueue(self, message: MailboxMessage) -> MailboxMessage:
        if not isinstance(message, MailboxMessage):
            raise CoordinationValidationError("message must be a MailboxMessage")
        if message.status is not MailboxStatus.PENDING or message.claimed_by is not None or message.attempt_count:
            raise CoordinationValidationError("new mailbox messages must be pending and unclaimed")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM mailbox WHERE idempotency_key=?",
                (message.idempotency_key,),
            ).fetchone()
            if existing is not None:
                restored = _message_from_row(existing)
                if _message_identity(restored) != _message_identity(message):
                    raise CoordinationConflict("idempotency key was reused for different mailbox intent")
                return restored
            existing_id = connection.execute(
                "SELECT * FROM mailbox WHERE message_id=?",
                (message.message_id,),
            ).fetchone()
            if existing_id is not None:
                raise CoordinationConflict("message id was reused for different mailbox intent")
            connection.execute(
                """INSERT INTO mailbox(
                    message_id, sender_role, sender_instance_id, sender_generation,
                    recipient_role, kind, subject, artifact_refs_json,
                    correlation_id, requires_ack, idempotency_key, created_at,
                    expires_at, status, claimed_by, claimed_at,
                    claim_lease_until, attempt_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message.message_id,
                    message.sender_role,
                    message.sender_instance_id,
                    message.sender_generation,
                    message.recipient_role,
                    message.kind.value,
                    message.subject,
                    _json([item.to_dict() for item in message.artifact_refs]),
                    message.correlation_id,
                    int(message.requires_ack),
                    message.idempotency_key,
                    message.created_at,
                    message.expires_at,
                    message.status.value,
                    message.claimed_by,
                    message.claimed_at,
                    message.claim_lease_until,
                    message.attempt_count,
                ),
            )
            return message

    def get_message(self, message_id: str) -> MailboxMessage | None:
        message_id = validate_identifier(message_id, "message_id")
        with self._lock:
            if self._closed:
                raise RuntimeError("coordination store is closed")
            row = self.connection.execute(
                "SELECT * FROM mailbox WHERE message_id=?",
                (message_id,),
            ).fetchone()
            return _message_from_row(row) if row is not None else None

    def list_messages(
        self,
        *,
        recipient_role: str | None = None,
        statuses: tuple[MailboxStatus | str, ...] | None = None,
        limit: int = _MAX_CLAIM_LIMIT,
    ) -> tuple[MailboxMessage, ...]:
        """Return a bounded mailbox projection without claiming messages.

        This is intentionally read-only.  Claim leases are reconciled only by
        the existing ``claim`` operation, so attach/recovery diagnostics do
        not mutate delivery state while inspecting it.
        """

        if recipient_role is not None:
            recipient_role = validate_identifier(recipient_role, "recipient_role")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= _MAX_CLAIM_LIMIT:
            raise CoordinationValidationError(f"limit must be between 1 and {_MAX_CLAIM_LIMIT}")
        normalized_statuses: tuple[MailboxStatus, ...] | None = None
        if statuses is not None:
            if isinstance(statuses, (str, bytes)) or not isinstance(statuses, tuple) or not statuses:
                raise CoordinationValidationError("statuses must be a non-empty tuple")
            try:
                normalized_statuses = tuple(
                    item if isinstance(item, MailboxStatus) else MailboxStatus(item)
                    for item in statuses
                )
            except (TypeError, ValueError) as exc:
                raise CoordinationValidationError("statuses contains an unsupported value") from exc
            if len(set(normalized_statuses)) != len(normalized_statuses):
                raise CoordinationValidationError("statuses must not contain duplicates")

        clauses: list[str] = []
        params: list[Any] = []
        if recipient_role is not None:
            clauses.append("recipient_role=?")
            params.append(recipient_role)
        if normalized_statuses is not None:
            placeholders = ", ".join("?" for _ in normalized_statuses)
            clauses.append(f"status IN ({placeholders})")
            params.extend(item.value for item in normalized_statuses)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._lock:
            if self._closed:
                raise RuntimeError("coordination store is closed")
            rows = self.connection.execute(
                f"SELECT * FROM mailbox{where} ORDER BY created_at, message_id LIMIT ?",
                (*params, limit),
            ).fetchall()
            return tuple(_message_from_row(row) for row in rows)

    def claim(
        self,
        recipient_role: str,
        *,
        consumer_instance_id: str,
        now: str,
        lease_seconds: int | float,
        limit: int = 10,
        kind: MessageKind | str | None = None,
    ) -> list[MailboxMessage]:
        recipient_role = validate_identifier(recipient_role, "recipient_role")
        consumer_instance_id = validate_identifier(consumer_instance_id, "consumer_instance_id")
        validate_timestamp(now, "now")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float)) or lease_seconds <= 0:
            raise CoordinationValidationError("lease_seconds must be positive")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= _MAX_CLAIM_LIMIT:
            raise CoordinationValidationError(f"limit must be between 1 and {_MAX_CLAIM_LIMIT}")
        normalized_kind = None if kind is None else (
            kind if isinstance(kind, MessageKind) else MessageKind(kind)
        )
        kind_value = normalized_kind.value if normalized_kind is not None else None
        now_dt = _parse_timestamp(now, "now")
        lease_until = datetime.fromtimestamp(
            now_dt.timestamp() + float(lease_seconds), tz=now_dt.tzinfo
        ).isoformat()
        claimed: list[MailboxMessage] = []
        with self._transaction() as connection:
            claimed_query = "SELECT * FROM mailbox WHERE recipient_role=? AND status=?"
            claimed_params: list[Any] = [recipient_role, MailboxStatus.CLAIMED.value]
            if kind_value is not None:
                claimed_query += " AND kind=?"
                claimed_params.append(kind_value)
            claimed_query += " ORDER BY created_at, message_id"
            rows = connection.execute(claimed_query, tuple(claimed_params)).fetchall()
            for row in rows:
                if _due(row["claim_lease_until"], now):
                    if _due(row["expires_at"], now):
                        connection.execute(
                            "UPDATE mailbox SET status=?, claimed_by=NULL, claimed_at=NULL, claim_lease_until=NULL WHERE message_id=?",
                            (MailboxStatus.EXPIRED.value, row["message_id"]),
                        )
                    else:
                        connection.execute(
                            "UPDATE mailbox SET status=?, claimed_by=NULL, claimed_at=NULL, claim_lease_until=NULL WHERE message_id=?",
                            (MailboxStatus.PENDING.value, row["message_id"]),
                        )
            pending_query = """SELECT * FROM mailbox
                   WHERE recipient_role=? AND status=?
                     AND (expires_at IS NULL OR expires_at > ?)"""
            pending_params: list[Any] = [recipient_role, MailboxStatus.PENDING.value, now]
            if kind_value is not None:
                pending_query += " AND kind=?"
                pending_params.append(kind_value)
            pending_query += " ORDER BY created_at, message_id LIMIT ?"
            pending_params.append(limit)
            pending_rows = connection.execute(pending_query, tuple(pending_params)).fetchall()
            for row in pending_rows:
                connection.execute(
                    """UPDATE mailbox
                       SET status=?, claimed_by=?, claimed_at=?, claim_lease_until=?, attempt_count=attempt_count+1
                       WHERE message_id=? AND status=?""",
                    (
                        MailboxStatus.CLAIMED.value,
                        consumer_instance_id,
                        now,
                        lease_until,
                        row["message_id"],
                        MailboxStatus.PENDING.value,
                    ),
                )
                updated = connection.execute(
                    "SELECT * FROM mailbox WHERE message_id=?",
                    (row["message_id"],),
                ).fetchone()
                claimed.append(_message_from_row(updated))
            expired_query = """SELECT * FROM mailbox
                   WHERE recipient_role=? AND status=? AND expires_at IS NOT NULL AND expires_at <= ?"""
            expired_params: list[Any] = [recipient_role, MailboxStatus.PENDING.value, now]
            if kind_value is not None:
                expired_query += " AND kind=?"
                expired_params.append(kind_value)
            expired_rows = connection.execute(expired_query, tuple(expired_params)).fetchall()
            for row in expired_rows:
                connection.execute(
                    "UPDATE mailbox SET status=? WHERE message_id=?",
                    (MailboxStatus.EXPIRED.value, row["message_id"]),
                )
        return claimed

    def ack(self, message_id: str, *, consumer_instance_id: str, now: str) -> MailboxMessage:
        message_id = validate_identifier(message_id, "message_id")
        consumer_instance_id = validate_identifier(consumer_instance_id, "consumer_instance_id")
        validate_timestamp(now, "now")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM mailbox WHERE message_id=?",
                (message_id,),
            ).fetchone()
            if row is None:
                raise CoordinationConflict("message does not exist")
            message = _message_from_row(row)
            if message.status is MailboxStatus.ACKED:
                return message
            if message.status is not MailboxStatus.CLAIMED:
                raise CoordinationConflict("only claimed messages can be acknowledged")
            if message.claimed_by != consumer_instance_id:
                raise CoordinationConflict("message claim belongs to another consumer")
            connection.execute(
                "UPDATE mailbox SET status=?, claim_lease_until=NULL WHERE message_id=?",
                (MailboxStatus.ACKED.value, message_id),
            )
            return _message_from_row(
                connection.execute("SELECT * FROM mailbox WHERE message_id=?", (message_id,)).fetchone()
            )

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self.connection.close()
                self._closed = True

    def __enter__(self) -> "CoordinationStore":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.close()


__all__ = ["CoordinationConflict", "CoordinationStore"]
