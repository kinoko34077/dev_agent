"""Machine-readable current-phase gate checker.

Exit 0: every item is VERIFIED. Exit 1: actionable work remains. Exit 2: only
external blockers remain (and no TODO/actionable item exists).
"""
from __future__ import annotations

import json
from pathlib import Path
import sys


def load_status(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") not in {1, 2, 3, 4}:
        raise ValueError("invalid gate status schema")
    return value


def check(value: dict) -> tuple[int, list[str]]:
    schema_version = value.get("schema_version")
    if schema_version in {2, 3, 4} and value.get("phase6_entry", "PROHIBITED_UNTIL_ALL_VERIFIED") != "ALL_VERIFIED":
        raise ValueError("schema v2/v3/v4 phase6_entry must require ALL_VERIFIED")
    if schema_version == 3:
        classifications = value.get("phase_classification")
        required_phases = {"phase3_5", "phase4", "phase5", "phase6", "phase6_future", "phase7_future"}
        if not isinstance(classifications, dict) or not required_phases <= set(classifications):
            raise ValueError("schema v3 requires phase_classification for phase3_5, phase4, phase5, phase6, phase6_future, and phase7_future")
    if schema_version == 4:
        classifications = value.get("phase_classification")
        required_phases = {"phase3_5", "phase4", "phase5", "phase6_foundation", "phase6_operational", "phase6_future", "phase7_future"}
        if not isinstance(classifications, dict) or not required_phases <= set(classifications):
            raise ValueError("schema v4 requires phase6_foundation and phase6_operational classifications")
        for phase, field in (("phase6_foundation", "phase6_foundation_status"), ("phase6_operational", "phase6_operational_status")):
            gate_statuses: list[str] = []
            for reference in classifications[phase].get("gates", []):
                try:
                    stage, item_id = reference.split("/", 1)
                    record = value["stages"][stage][item_id]
                except (AttributeError, KeyError, ValueError, TypeError) as exc:
                    raise ValueError(f"invalid {phase} gate reference: {reference}") from exc
                gate_statuses.append(record.get("status", "MISSING_STATUS"))
            derived = "VERIFIED" if gate_statuses and all(status == "VERIFIED" for status in gate_statuses) else "IN_PROGRESS"
            if value.get(field) != derived:
                raise ValueError(f"{field} must equal derived status {derived}")
    pending: list[str] = []
    blockers: list[str] = []
    for stage, items in value.get("stages", {}).items():
        if not isinstance(items, dict):
            raise ValueError(f"invalid stage: {stage}")
        for item_id, record in items.items():
            if not isinstance(record, dict):
                raise ValueError(f"invalid gate record: {stage}/{item_id}")
            status = record.get("status")
            if schema_version in {3, 4} and status == "DEFERRED" and not record.get("actionable", False):
                continue
            # Schema v1 used PASS.  It is accepted only for old callers; the
            # v2 status file uses VERIFIED so existence is never mistaken for
            # integration or verification.
            if status == "VERIFIED" or (status == "PASS" and value.get("schema_version") == 1):
                if schema_version in {2, 3, 4} and not record.get("evidence"):
                    raise ValueError(f"verified gate has no evidence: {stage}/{item_id}")
                continue
            if status == "BLOCKED" and not record.get("actionable", False):
                blockers.append(f"{stage}/{item_id}: {record.get('blocker', 'external blocker')}")
            else:
                pending.append(f"{stage}/{item_id} ({status or 'MISSING_STATUS'})")
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
    print("PASS: all current gates" if code == 0 else ("ACTIONABLE: " if code == 1 else "BLOCKED: ") + ", ".join(details))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
