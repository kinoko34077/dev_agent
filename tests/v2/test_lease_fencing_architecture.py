from pathlib import Path

from src.dev_agent.persistence.lease import LeaseProof, StaleLease
from src.dev_agent.scheduler.queue import LeaseProof as QueueLeaseProof
from src.dev_agent.scheduler.queue import StaleLease as QueueStaleLease
from src.dev_agent.state.sqlite_store import SQLiteStateStore
from src.dev_agent.state.views import EventStore, TaskStateView


def test_scheduler_lease_types_are_reexported_from_neutral_persistence():
    assert QueueLeaseProof is LeaseProof
    assert QueueStaleLease is StaleLease


def test_state_store_does_not_import_concrete_scheduler_module():
    source = Path("src/dev_agent/state/sqlite_store.py").read_text(encoding="utf-8")

    assert "scheduler.queue" not in source


def test_sqlite_state_store_satisfies_narrow_event_and_task_views(tmp_path):
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        assert isinstance(store, EventStore)
        assert isinstance(store, TaskStateView)
