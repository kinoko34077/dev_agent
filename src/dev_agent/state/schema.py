"""Versioned SQLite state schema and migrations."""

from __future__ import annotations

import sqlite3


class StateSchema:
    """Own state-table DDL while SQLiteStateStore owns the connection."""

    VERSION = 10

    V1_SCHEMA = """
        CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS steps (step_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tool_results (call_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS checkpoints (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, step_id TEXT NOT NULL, phase TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS idempotency (idempotency_key TEXT PRIMARY KEY, result_payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals (approval_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, side_effect_level TEXT NOT NULL, actor TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """

    LATEST_SCHEMA = """
        CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS steps (step_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tool_results (call_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS checkpoints (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, step_id TEXT NOT NULL, phase TEXT NOT NULL, state_payload TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS idempotency (idempotency_key TEXT PRIMARY KEY, result_payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals (approval_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, side_effect_level TEXT NOT NULL, actor TEXT NOT NULL, call_id TEXT NOT NULL, arguments_hash TEXT NOT NULL, expires_at REAL, revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS effect_intents (idempotency_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, tool_name TEXT NOT NULL, arguments_payload TEXT NOT NULL, status TEXT NOT NULL, result_payload TEXT);
        CREATE TABLE IF NOT EXISTS approval_consumptions (approval_id TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS effect_reconciliations (sequence INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT NOT NULL, status TEXT NOT NULL, actor TEXT NOT NULL, source TEXT NOT NULL, external_id TEXT, evidence_payload TEXT NOT NULL, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS provider_dispatch_audits (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, request_id TEXT NOT NULL, intent_key TEXT, provider_id TEXT NOT NULL, resource_id TEXT NOT NULL, native_unit TEXT NOT NULL, estimated_cost_minor INTEGER, price_currency TEXT, outcome TEXT NOT NULL, details_payload TEXT NOT NULL DEFAULT '{}', recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_provider_dispatch_audits_task ON provider_dispatch_audits(task_id, sequence);
        CREATE INDEX IF NOT EXISTS idx_provider_dispatch_audits_request ON provider_dispatch_audits(request_id, sequence);
        CREATE TABLE IF NOT EXISTS task_controls (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, control_type TEXT NOT NULL, reason TEXT NOT NULL, requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_task_controls_task ON task_controls(task_id, sequence);
        CREATE TABLE IF NOT EXISTS human_requests (request_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, status TEXT NOT NULL, request_payload TEXT NOT NULL, response_payload TEXT, requested_at TEXT NOT NULL, responded_at TEXT, consumed_at TEXT);
        CREATE INDEX IF NOT EXISTS idx_human_requests_task ON human_requests(task_id, status);
        CREATE TABLE IF NOT EXISTS discord_bindings (binding_key TEXT PRIMARY KEY, guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, thread_id TEXT NOT NULL, root_id TEXT NOT NULL, run_id TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_discord_bindings_root ON discord_bindings(root_id, run_id);
        CREATE TABLE IF NOT EXISTS discord_ingress (message_id TEXT PRIMARY KEY, binding_key TEXT NOT NULL, kind TEXT NOT NULL, received_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_discord_ingress_binding ON discord_ingress(binding_key, received_at);
        CREATE TABLE IF NOT EXISTS discord_deliveries (request_id TEXT NOT NULL, discord_message_id TEXT NOT NULL, delivered_at TEXT NOT NULL, PRIMARY KEY(request_id, discord_message_id));
        CREATE TABLE IF NOT EXISTS discord_scopes (binding_key TEXT PRIMARY KEY, directory_scope TEXT, selected_files_payload TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS discord_conversation_messages (
            message_id TEXT PRIMARY KEY,
            binding_key TEXT NOT NULL,
            guild_id TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            received_at TEXT NOT NULL,
            speaker_role TEXT NOT NULL,
            speaker_id TEXT NOT NULL,
            speaker_name TEXT NOT NULL,
            direction TEXT NOT NULL,
            content TEXT NOT NULL,
            reply_to_message_id TEXT,
            message_kind TEXT NOT NULL,
            root_id TEXT,
            run_id TEXT,
            source TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_discord_conversation_binding ON discord_conversation_messages(binding_key, received_at, message_id);
        CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    @classmethod
    def initialize(cls, connection: sqlite3.Connection) -> None:
        """Create a fresh latest schema or apply ordered migrations."""
        try:
            user_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
            if not user_tables:
                # Fresh databases are created at the latest schema directly;
                # only existing databases pass through ordered migrations.
                connection.executescript(cls.LATEST_SCHEMA)
                connection.execute("INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)", (str(cls.VERSION),))
                connection.commit()
                return
            connection.executescript(cls.V1_SCHEMA)
            connection.execute("INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '1')")
            current = int(connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0])
            if current > cls.VERSION:
                raise ValueError(f"unsupported state schema version: {current}")
            connection.commit()
            connection.execute("BEGIN")
            if current < 2:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(checkpoints)")}
                if "state_payload" not in columns:
                    connection.execute("ALTER TABLE checkpoints ADD COLUMN state_payload TEXT NOT NULL DEFAULT '{}'")
                approval_columns = {row[1] for row in connection.execute("PRAGMA table_info(approvals)")}
                for name in ("call_id", "arguments_hash"):
                    if name not in approval_columns:
                        connection.execute(f"ALTER TABLE approvals ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
                connection.execute("CREATE TABLE IF NOT EXISTS effect_intents (idempotency_key TEXT PRIMARY KEY, task_id TEXT NOT NULL, tool_name TEXT NOT NULL, arguments_payload TEXT NOT NULL, status TEXT NOT NULL, result_payload TEXT)")
                connection.execute("UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'")
                current = 2
            if current < 3:
                approval_columns = {row[1] for row in connection.execute("PRAGMA table_info(approvals)")}
                if "expires_at" not in approval_columns:
                    connection.execute("ALTER TABLE approvals ADD COLUMN expires_at REAL")
                if "revoked" not in approval_columns:
                    connection.execute("ALTER TABLE approvals ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0")
                connection.execute("CREATE TABLE IF NOT EXISTS approval_consumptions (approval_id TEXT PRIMARY KEY)")
                connection.execute("UPDATE schema_meta SET value = '3' WHERE key = 'schema_version'")
                current = 3
            if current < 4:
                connection.execute("CREATE TABLE IF NOT EXISTS effect_reconciliations (sequence INTEGER PRIMARY KEY AUTOINCREMENT, idempotency_key TEXT NOT NULL, status TEXT NOT NULL, actor TEXT NOT NULL, source TEXT NOT NULL, external_id TEXT, evidence_payload TEXT NOT NULL, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                connection.execute("UPDATE schema_meta SET value = '4' WHERE key = 'schema_version'")
                current = 4
            if current < 5:
                connection.execute("CREATE TABLE IF NOT EXISTS provider_dispatch_audits (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, request_id TEXT NOT NULL, intent_key TEXT, provider_id TEXT NOT NULL, resource_id TEXT NOT NULL, native_unit TEXT NOT NULL, estimated_cost_minor INTEGER, price_currency TEXT, outcome TEXT NOT NULL, details_payload TEXT NOT NULL DEFAULT '{}', recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_provider_dispatch_audits_task ON provider_dispatch_audits(task_id, sequence)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_provider_dispatch_audits_request ON provider_dispatch_audits(request_id, sequence)")
                connection.execute("UPDATE schema_meta SET value = '5' WHERE key = 'schema_version'")
                current = 5
                if current < 6:
                    connection.execute("CREATE TABLE IF NOT EXISTS task_controls (sequence INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, control_type TEXT NOT NULL, reason TEXT NOT NULL, requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
                    connection.execute("CREATE INDEX IF NOT EXISTS idx_task_controls_task ON task_controls(task_id, sequence)")
                    connection.execute("UPDATE schema_meta SET value = '6' WHERE key = 'schema_version'")
                    current = 6
            if current < 7:
                connection.execute("CREATE TABLE IF NOT EXISTS human_requests (request_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, status TEXT NOT NULL, request_payload TEXT NOT NULL, response_payload TEXT, requested_at TEXT NOT NULL, responded_at TEXT, consumed_at TEXT)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_human_requests_task ON human_requests(task_id, status)")
                connection.execute("UPDATE schema_meta SET value = '7' WHERE key = 'schema_version'")
                current = 7
            if current < 8:
                connection.execute("CREATE TABLE IF NOT EXISTS discord_bindings (binding_key TEXT PRIMARY KEY, guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, thread_id TEXT NOT NULL, root_id TEXT NOT NULL, run_id TEXT NOT NULL, updated_at TEXT NOT NULL)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_discord_bindings_root ON discord_bindings(root_id, run_id)")
                connection.execute("CREATE TABLE IF NOT EXISTS discord_ingress (message_id TEXT PRIMARY KEY, binding_key TEXT NOT NULL, kind TEXT NOT NULL, received_at TEXT NOT NULL)")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_discord_ingress_binding ON discord_ingress(binding_key, received_at)")
                connection.execute("CREATE TABLE IF NOT EXISTS discord_deliveries (request_id TEXT NOT NULL, discord_message_id TEXT NOT NULL, delivered_at TEXT NOT NULL, PRIMARY KEY(request_id, discord_message_id))")
                connection.execute("UPDATE schema_meta SET value = '8' WHERE key = 'schema_version'")
                current = 8
            if current < 9:
                connection.execute("CREATE TABLE IF NOT EXISTS discord_scopes (binding_key TEXT PRIMARY KEY, directory_scope TEXT, selected_files_payload TEXT NOT NULL DEFAULT '[]', updated_at TEXT NOT NULL)")
                connection.execute("UPDATE schema_meta SET value = '9' WHERE key = 'schema_version'")
                current = 9
            if current < 10:
                connection.execute("""CREATE TABLE IF NOT EXISTS discord_conversation_messages (
                    message_id TEXT PRIMARY KEY,
                    binding_key TEXT NOT NULL,
                    guild_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    speaker_role TEXT NOT NULL,
                    speaker_id TEXT NOT NULL,
                    speaker_name TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    content TEXT NOT NULL,
                    reply_to_message_id TEXT,
                    message_kind TEXT NOT NULL,
                    root_id TEXT,
                    run_id TEXT,
                    source TEXT NOT NULL
                )""")
                connection.execute("CREATE INDEX IF NOT EXISTS idx_discord_conversation_binding ON discord_conversation_messages(binding_key, received_at, message_id)")
                connection.execute("UPDATE schema_meta SET value = '10' WHERE key = 'schema_version'")
            connection.commit()
        except Exception:
            connection.rollback()
            raise


__all__ = ["StateSchema"]
