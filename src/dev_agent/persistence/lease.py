"""Neutral lease-fencing primitives for shared SQLite transactions."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import time


class StaleLease(RuntimeError):
    """The caller no longer owns the durable queue lease."""


@dataclass(frozen=True)
class LeaseProof:
    task_id: str
    worker_id: str
    lease_token: str
    state_version: int


def assert_active_lease(connection: sqlite3.Connection, proof: LeaseProof) -> None:
    """Assert a proof against the queue row in the caller's transaction.

    State and Scheduler share the same SQLite transaction boundary, but State
    must not import the Scheduler implementation.  A missing queue table is
    preserved as the historical no-fence case for standalone state stores.
    """

    if not isinstance(proof, LeaseProof):
        raise TypeError("proof must be a LeaseProof")
    table = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='queue_items'"
    ).fetchone()
    if table is None:
        return
    row = connection.execute(
        """SELECT 1 FROM queue_items
           WHERE task_id=? AND state='leased' AND lease_owner=?
             AND lease_token=? AND state_version=? AND lease_until > ?""",
        (proof.task_id, proof.worker_id, proof.lease_token, proof.state_version, time.time()),
    ).fetchone()
    if row is None:
        raise StaleLease(proof.task_id)


__all__ = ["LeaseProof", "StaleLease", "assert_active_lease"]
