"""Read-only SQLite state validation without importing the normal Runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys


REQUIRED_TABLES = frozenset({"tasks", "steps", "tool_results", "events", "checkpoints", "idempotency", "approvals", "approval_consumptions", "effect_intents", "schema_meta"})


def validate_sqlite_state(path: str | Path) -> tuple[bool, str]:
    database = Path(path)
    try:
        connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return False, f"cannot open SQLite state read-only: {exc}"
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = sorted(REQUIRED_TABLES - tables)
        if missing:
            return False, f"missing tables: {', '.join(missing)}"
        version_row = connection.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        if version_row is None or version_row[0] != "2":
            return False, "unsupported or missing schema version"
        invalid = connection.execute("SELECT task_id, payload FROM tasks").fetchall()
        task_ids = set()
        for task_id, payload in invalid:
            value = json.loads(payload)
            if not isinstance(value, dict) or value.get("task_id") != task_id or "status" not in value:
                return False, f"invalid task payload: {task_id}"
            task_ids.add(task_id)
        for step_id, payload in connection.execute("SELECT step_id, payload FROM steps"):
            value = json.loads(payload)
            if not isinstance(value, dict) or value.get("step_id") != step_id or value.get("task_id") not in task_ids:
                return False, f"orphan or invalid step: {step_id}"
        for task_id, state_payload in connection.execute("SELECT task_id, state_payload FROM checkpoints"):
            if task_id not in task_ids:
                return False, f"orphan checkpoint task: {task_id}"
            state = json.loads(state_payload)
            if not isinstance(state, dict):
                return False, f"invalid checkpoint state: {task_id}"
        for call_id, payload in connection.execute("SELECT call_id, payload FROM tool_results"):
            value = json.loads(payload)
            if not isinstance(value, dict) or value.get("call_id") != call_id:
                return False, f"invalid tool result: {call_id}"
        approval_columns = {row[1] for row in connection.execute("PRAGMA table_info(approvals)")}
        required_approval_columns = {"approval_id", "task_id", "side_effect_level", "actor", "call_id", "arguments_hash", "expires_at", "revoked"}
        if not required_approval_columns <= approval_columns:
            return False, "invalid approvals schema"
        for approval_id, task_id, level, actor, call_id, arguments_hash, expires_at, revoked in connection.execute("SELECT approval_id, task_id, side_effect_level, actor, call_id, arguments_hash, expires_at, revoked FROM approvals"):
            if not all(isinstance(value, str) and value.strip() for value in (approval_id, task_id, level, actor, call_id, arguments_hash)) or revoked not in (0, 1):
                return False, f"invalid approval record: {approval_id}"
        intent_columns = {row[1] for row in connection.execute("PRAGMA table_info(effect_intents)")}
        if not {"idempotency_key", "task_id", "tool_name", "arguments_payload", "status"} <= intent_columns:
            return False, "invalid effect_intents schema"
        for key, task_id, tool_name, arguments_payload, status, result_payload in connection.execute("SELECT idempotency_key, task_id, tool_name, arguments_payload, status, result_payload FROM effect_intents"):
            if task_id not in task_ids or not tool_name.strip() or status not in {"pending", "succeeded", "unknown"}:
                return False, f"invalid effect intent: {key}"
            if not isinstance(json.loads(arguments_payload), dict):
                return False, f"invalid effect intent arguments: {key}"
            if status in {"succeeded", "unknown"} and not result_payload:
                return False, f"completed effect intent has no result: {key}"
        return True, "SQLite state schema and task payloads are readable"
    except (sqlite3.Error, json.JSONDecodeError) as exc:
        return False, f"cannot validate SQLite state: {exc}"
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate dev_agent SQLite state without Runtime imports")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    ok, message = validate_sqlite_state(args.path)
    print(f"{'PASS' if ok else 'FAIL'}: {message}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
