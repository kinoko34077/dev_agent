"""Durable SQLite store for process presence and mailbox delivery."""

from __future__ import annotations

from contextlib import contextmanager
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
    MailboxMessage,
    MailboxStatus,
    PeerRecord,
    PeerStatus,
)
from .protocol_helpers import validate_identifier, validate_timestamp


_MAX_CLAIM_LIMIT = 64


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
                CREATE INDEX IF NOT EXISTS mailbox_recipient_status_idx
                    ON mailbox(recipient_role, status, created_at, message_id);
                CREATE INDEX IF NOT EXISTS mailbox_claim_lease_idx
                    ON mailbox(status, claim_lease_until);
                """
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO coordination_meta(key, value) VALUES('schema_version', '1')"
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

    def claim(
        self,
        recipient_role: str,
        *,
        consumer_instance_id: str,
        now: str,
        lease_seconds: int | float,
        limit: int = 10,
    ) -> list[MailboxMessage]:
        recipient_role = validate_identifier(recipient_role, "recipient_role")
        consumer_instance_id = validate_identifier(consumer_instance_id, "consumer_instance_id")
        validate_timestamp(now, "now")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, (int, float)) or lease_seconds <= 0:
            raise CoordinationValidationError("lease_seconds must be positive")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= _MAX_CLAIM_LIMIT:
            raise CoordinationValidationError(f"limit must be between 1 and {_MAX_CLAIM_LIMIT}")
        now_dt = _parse_timestamp(now, "now")
        lease_until = datetime.fromtimestamp(
            now_dt.timestamp() + float(lease_seconds), tz=now_dt.tzinfo
        ).isoformat()
        claimed: list[MailboxMessage] = []
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM mailbox WHERE recipient_role=? AND status=? ORDER BY created_at, message_id",
                (recipient_role, MailboxStatus.CLAIMED.value),
            ).fetchall()
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
            pending_rows = connection.execute(
                """SELECT * FROM mailbox
                   WHERE recipient_role=? AND status=?
                     AND (expires_at IS NULL OR expires_at > ?)
                   ORDER BY created_at, message_id LIMIT ?""",
                (recipient_role, MailboxStatus.PENDING.value, now, limit),
            ).fetchall()
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
            expired_rows = connection.execute(
                """SELECT * FROM mailbox
                   WHERE recipient_role=? AND status=? AND expires_at IS NOT NULL AND expires_at <= ?""",
                (recipient_role, MailboxStatus.PENDING.value, now),
            ).fetchall()
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
