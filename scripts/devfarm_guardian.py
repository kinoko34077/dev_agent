"""Bounded Guardian operator entrypoint.

This is a thin operator boundary over the existing Process Coordination and
Guardian services.  It validates Host-owned static launch profiles, reports a
bounded health projection, and performs one restart-reconciliation pass.  It
does not install an OS service, accept mailbox shell commands, or create a
second scheduler.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

from src.dev_agent.coordination.guardian import GuardianActionService
from src.dev_agent.coordination.guardian_process import LaunchProfile
from src.dev_agent.coordination.store import CoordinationStore


class GuardianOperatorError(ValueError):
    """The static Guardian operator configuration is invalid."""


_PROFILE_FIELDS = frozenset(
    {
        "profile_id",
        "role",
        "generation",
        "revision",
        "executable",
        "arguments",
        "runtime_root",
        "environment_profile",
    }
)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardianOperatorError("Guardian configuration is unreadable") from exc
    if not isinstance(value, dict):
        raise GuardianOperatorError("Guardian configuration must be an object")
    return value


def load_launch_profiles(path: str | Path) -> tuple[LaunchProfile, ...]:
    """Load only Host-authored static launch profiles from JSON."""

    document = _read_json(Path(path).resolve())
    unknown = set(document) - {"profiles"}
    if unknown:
        raise GuardianOperatorError(f"unknown Guardian configuration field: {sorted(unknown)[0]}")
    profiles = document.get("profiles")
    if isinstance(profiles, (str, bytes)) or not isinstance(profiles, list) or not profiles:
        raise GuardianOperatorError("Guardian configuration profiles must be a non-empty list")
    result: list[LaunchProfile] = []
    for item in profiles:
        if not isinstance(item, dict):
            raise GuardianOperatorError("Guardian profile must be an object")
        unknown_profile = set(item) - _PROFILE_FIELDS
        if unknown_profile:
            raise GuardianOperatorError(f"unknown Guardian profile field: {sorted(unknown_profile)[0]}")
        try:
            result.append(LaunchProfile(**item))
        except (TypeError, ValueError) as exc:
            raise GuardianOperatorError("Guardian profile failed Host validation") from exc
    keys = [(profile.role, profile.generation) for profile in result]
    if len(set(keys)) != len(keys):
        raise GuardianOperatorError("Guardian profiles must have unique role/generation pairs")
    return tuple(result)


def guardian_health(config_path: str | Path) -> dict[str, Any]:
    """Return a bounded diagnostic suitable for an OS liveness wrapper."""

    profiles = load_launch_profiles(config_path)
    return {
        "status": "READY",
        "component": "guardian",
        "platform": sys.platform,
        "profile_count": len(profiles),
        "roles": sorted({profile.role for profile in profiles}),
        "process_authority": "static_profile_only",
        "task_scheduler": "not_owned_by_guardian",
        "os_registration": "NOT_CONFIGURED",
        "network": "not_used",
    }


def guardian_run_once(config_path: str | Path, data_dir: str | Path) -> dict[str, Any]:
    """Validate profiles and reconcile interrupted Guardian effects once."""

    health = guardian_health(config_path)
    root = Path(data_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with CoordinationStore(root / "coordination.sqlite3") as store:
        reconciled = GuardianActionService(store).reconcile_all_interrupted(limit=64)
    return {
        **health,
        "mode": "run_once",
        "reconciled_action_count": len(reconciled),
        "bounded": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Host-owned bounded Guardian operator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    health = subparsers.add_parser("health")
    health.add_argument("--config", required=True, type=Path)
    run = subparsers.add_parser("run")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DEV_AGENT_DATA_DIR", ".dev_agent")))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "health":
            result = guardian_health(args.config)
        else:
            result = guardian_run_once(args.config, args.data_dir)
    except GuardianOperatorError as exc:
        print(json.dumps({"status": "REJECTED", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["GuardianOperatorError", "guardian_health", "guardian_run_once", "load_launch_profiles", "main"]
