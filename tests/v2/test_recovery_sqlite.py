from recovery.validate_sqlite_state import validate_sqlite_state
import sqlite3
from src.dev_agent.domain.protocol import Task
from src.dev_agent.state import SQLiteStateStore
from recovery.backup import backup_sqlite, maintenance_lock, restore_sqlite, validate_backup
import pytest


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
        store.connection.execute("INSERT INTO approvals(approval_id, task_id, side_effect_level, actor, call_id, arguments_hash) VALUES ('approval-1', 'task-1', 'financial', '', 'call-1', 'hash')")
        store.connection.commit()
    ok, message = validate_sqlite_state(database)
    assert not ok
    assert "approval" in message


def test_sqlite_store_records_schema_version(tmp_path):
    database = tmp_path / "versioned.sqlite3"
    with SQLiteStateStore(database) as store:
        version = store.connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0]
    assert version == "4"


def test_sqlite_store_upgrades_a_v1_schema_through_ordered_migrations(tmp_path):
    database = tmp_path / "v1.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE steps (step_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE tool_results (call_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE checkpoints (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, step_id TEXT NOT NULL, phase TEXT NOT NULL);
            CREATE TABLE idempotency (idempotency_key TEXT PRIMARY KEY, result_payload TEXT NOT NULL);
            CREATE TABLE approvals (approval_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, side_effect_level TEXT NOT NULL, actor TEXT NOT NULL);
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES ('schema_version', '1');
            """
        )
    with SQLiteStateStore(database) as store:
        assert store.connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0] == "4"
        assert {row[1] for row in store.connection.execute("PRAGMA table_info(approvals)")} >= {"call_id", "arguments_hash", "expires_at", "revoked"}
        assert "state_payload" in {row[1] for row in store.connection.execute("PRAGMA table_info(checkpoints)")}
        assert store.connection.execute("SELECT 1 FROM effect_intents").fetchone() is None


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


def test_recovery_backup_restore_and_maintenance_lock(tmp_path):
    source = tmp_path / "source.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    with SQLiteStateStore(source) as store:
        task = Task(objective="backup")
        store.save_task(task)
    backup_sqlite(source, backup)
    assert validate_backup(source, backup)
    restore_sqlite(backup, restored)
    assert validate_backup(source, restored)
    with SQLiteStateStore(restored) as store:
        assert store.load_task(task.task_id).objective == "backup"
    with pytest.raises(FileExistsError):
        restore_sqlite(backup, restored)
    lock = tmp_path / "maintenance.lock"
    with maintenance_lock(lock):
        assert lock.exists()
        with pytest.raises(FileExistsError):
            with maintenance_lock(lock):
                pass
        assert lock.exists()
    assert not lock.exists()
