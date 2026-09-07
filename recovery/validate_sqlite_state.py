"""Read-only SQLite state validation without importing the normal Runtime."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys


REQUIRED_TABLES = frozenset({"tasks", "steps", "tool_results", "events", "checkpoints", "idempotency", "approvals", "effect_intents", "schema_meta"})


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
        for task_id, payload in invalid:
            value = json.loads(payload)
            if not isinstance(value, dict) or value.get("task_id") != task_id or "status" not in value:
                return False, f"invalid task payload: {task_id}"
        approval_columns = {row[1] for row in connection.execute("PRAGMA table_info(approvals)")}
        required_approval_columns = {"approval_id", "task_id", "side_effect_level", "actor"}
        if not required_approval_columns <= approval_columns:
            return False, "invalid approvals schema"
        for approval_id, task_id, level, actor in connection.execute("SELECT approval_id, task_id, side_effect_level, actor FROM approvals"):
            if not all(isinstance(value, str) and value.strip() for value in (approval_id, task_id, level, actor)):
                return False, f"invalid approval record: {approval_id}"
        intent_columns = {row[1] for row in connection.execute("PRAGMA table_info(effect_intents)")}
        if not {"idempotency_key", "task_id", "tool_name", "arguments_payload", "status"} <= intent_columns:
            return False, "invalid effect_intents schema"
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
