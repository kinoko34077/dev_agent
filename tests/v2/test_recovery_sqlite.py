from recovery.validate_sqlite_state import validate_sqlite_state
from src.dev_agent.domain.protocol import Task
from src.dev_agent.state import SQLiteStateStore


def test_recovery_validates_sqlite_without_runtime_import(tmp_path):
    database = tmp_path / "state.sqlite3"
    with SQLiteStateStore(database) as store:
        task = Task(objective="recovery inspection")
        store.save_task(task)
    assert validate_sqlite_state(database) == (True, "SQLite state schema and task payloads are readable")


def test_recovery_rejects_non_database(tmp_path):
    invalid = tmp_path / "not-a-db.sqlite3"
    invalid.write_text("not sqlite", encoding="utf-8")
    ok, message = validate_sqlite_state(invalid)
    assert not ok
    assert "SQLite" in message or "validate" in message


def test_recovery_rejects_state_without_approval_table(tmp_path):
    database = tmp_path / "missing-approvals.sqlite3"
    with SQLiteStateStore(database) as store:
        store.connection.execute("DROP TABLE approvals")
        store.connection.commit()
    ok, message = validate_sqlite_state(database)
    assert not ok
    assert "approvals" in message


def test_recovery_rejects_malformed_approval_record(tmp_path):
    database = tmp_path / "bad-approval.sqlite3"
    with SQLiteStateStore(database) as store:
        store.connection.execute("INSERT INTO approvals VALUES ('approval-1', 'task-1', 'financial', '')")
        store.connection.commit()
    ok, message = validate_sqlite_state(database)
    assert not ok
    assert "approval" in message
