"""Narrow, runtime-independent recovery command surface."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .diagnose import run_diagnostics
from .phase6_recovery import RecoveryOperator


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only-first recovery rescue utility")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("diagnose", "validate-ledger", "backup", "backup-artifacts", "restore-plan", "rollback-plan", "repair-branch-plan"):
        command = sub.add_parser(name)
        command.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
        command.add_argument("--json", action="store_true")
        command.add_argument("--allow-write", action="store_true")
    sub.choices["diagnose"].add_argument("--resource-ledger", type=Path)
    sub.choices["validate-ledger"].add_argument("database", type=Path)
    sub.choices["backup"].add_argument("source", type=Path)
    sub.choices["backup"].add_argument("destination", type=Path)
    sub.choices["backup-artifacts"].add_argument("source", type=Path)
    sub.choices["backup-artifacts"].add_argument("destination", type=Path)
    sub.choices["restore-plan"].add_argument("metadata", type=Path)
    sub.choices["rollback-plan"].add_argument("metadata", type=Path)
    sub.choices["repair-branch-plan"].add_argument("metadata", type=Path)
    sub.choices["repair-branch-plan"].add_argument("branch")
    args = parser.parse_args(argv)
    operator = RecoveryOperator(args.root)
    if args.command == "diagnose":
        value = [asdict(item) for item in run_diagnostics(args.root, resource_ledger=args.resource_ledger)]
    elif args.command == "validate-ledger":
        ok, detail = operator.validate_state(args.database)
        value = {"ok": ok, "detail": detail}
    elif args.command == "backup":
        if not args.allow_write:
            parser.error("backup requires --allow-write")
        value = {"path": str(operator.backup_state(args.source, args.destination))}
    elif args.command == "backup-artifacts":
        if not args.allow_write:
            parser.error("backup-artifacts requires --allow-write")
        value = {"path": str(operator.backup_artifacts(args.source, args.destination))}
    elif args.command == "restore-plan":
        value = {"operation": "restore", "metadata": str(args.metadata), "mutation_requires": "--allow-write"}
    elif args.command == "rollback-plan":
        value = asdict(operator.rollback_plan(args.metadata))
    else:
        value = {"operation": "repair-branch", "metadata": str(args.metadata), "branch": args.branch, "mutation_requires": "--allow-write"}
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str) if args.json else json.dumps(value, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
