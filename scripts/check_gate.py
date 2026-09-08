"""Machine-readable Stage A-E gate checker.

Exit 0: every item PASS. Exit 1: actionable work remains. Exit 2: only
external blockers remain (and no TODO/actionable item exists).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys


def load_status(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("invalid gate status schema")
    return value


def check(value: dict) -> tuple[int, list[str]]:
    pending: list[str] = []
    blockers: list[str] = []
    for stage, items in value.get("stages", {}).items():
        for item_id, record in items.items():
            status = record.get("status")
            if status == "PASS":
                continue
            if status == "BLOCKED" and not record.get("actionable", False):
                blockers.append(f"{stage}/{item_id}: {record.get('blocker', 'external blocker')}")
            else:
                pending.append(f"{stage}/{item_id}")
    if pending:
        return 1, pending + (["external blockers: " + "; ".join(blockers)] if blockers else [])
    if blockers:
        return 2, blockers
    return 0, []


def main(argv: list[str] | None = None) -> int:
    path = Path(argv[0]) if argv else Path(__file__).resolve().parents[1] / "spec" / "v2" / "GATE_STATUS.json"
    try:
        code, details = check(load_status(path))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"GATE_STATUS_ERROR: {exc}")
        return 1
    print("PASS: all Stage A-E gates" if code == 0 else ("ACTIONABLE: " if code == 1 else "BLOCKED: ") + ", ".join(details))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
