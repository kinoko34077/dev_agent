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


def test_sqlite_store_records_schema_version(tmp_path):
    database = tmp_path / "versioned.sqlite3"
    with SQLiteStateStore(database) as store:
        version = store.connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0]
    assert version == "2"


def test_recovery_rejects_unsupported_schema_version(tmp_path):
    database = tmp_path / "future.sqlite3"
    with SQLiteStateStore(database) as store:
        store.connection.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
        store.connection.commit()
    ok, message = validate_sqlite_state(database)
    assert not ok
    assert "schema version" in message


def test_recovery_rejects_orphan_step(tmp_path):
    database = tmp_path / "orphan-step.sqlite3"
    with SQLiteStateStore(database) as store:
        store.connection.execute("INSERT INTO steps VALUES ('step-1', '{\"step_id\":\"step-1\",\"task_id\":\"11111111-1111-4111-8111-111111111111\",\"kind\":\"model\"}')")
        store.connection.commit()
    ok, message = validate_sqlite_state(database)
    assert not ok
    assert "orphan" in message


def test_recovery_rejects_unknown_effect_intent_status(tmp_path):
    database = tmp_path / "bad-intent.sqlite3"
    task = Task(objective="intent")
    with SQLiteStateStore(database) as store:
        store.save_task(task)
        store.connection.execute("INSERT INTO effect_intents VALUES ('k', ?, 'publish', '{}', 'mystery', NULL)", (task.task_id,))
        store.connection.commit()
    ok, message = validate_sqlite_state(database)
    assert not ok
    assert "effect intent" in message
