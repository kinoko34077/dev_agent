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
import re
import subprocess
import sys
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
_TASK_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


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


def guardian_serve(
    config_path: str | Path,
    data_dir: str | Path,
    *,
    poll_seconds: float = 6.0,
    max_cycles: int | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run the existing Guardian reconciliation service at a bounded cadence.

    This is a liveness loop, not a Task Scheduler: each cycle delegates to
    ``guardian_run_once`` and owns no Task claim, retry, or planning state.
    ``max_cycles`` exists for deterministic drills and tests; an OS wrapper
    may omit it and stop the process externally.
    """

    if isinstance(poll_seconds, bool) or not isinstance(poll_seconds, (int, float)):
        raise GuardianOperatorError("poll_seconds must be numeric")
    if poll_seconds < 1.0 or poll_seconds > 3600.0:
        raise GuardianOperatorError("poll_seconds must be between 1 and 3600 seconds")
    if max_cycles is not None and (
        isinstance(max_cycles, bool) or not isinstance(max_cycles, int) or max_cycles <= 0
    ):
        raise GuardianOperatorError("max_cycles must be a positive integer")
    if not callable(sleep_fn):
        raise GuardianOperatorError("sleep_fn must be callable")

    result: dict[str, Any] = {}
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        result = guardian_run_once(config_path, data_dir)
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        sleep_fn(float(poll_seconds))
    return {
        **result,
        "mode": "serve",
        "cycles": cycles,
        "poll_seconds": float(poll_seconds),
        "bounded": max_cycles is not None,
    }


def guardian_registration(
    config_path: str | Path,
    data_dir: str | Path,
    *,
    task_name: str = "DevAgentGuardian",
    apply: bool = False,
) -> dict[str, Any]:
    """Build or explicitly apply one static Windows liveness registration.

    The default is a read-only specification.  ``apply=True`` is the only
    path that invokes ``schtasks.exe`` and is deliberately unavailable on
    non-Windows hosts.  The command is assembled from this checked-in module;
    no mailbox or caller-supplied arbitrary command is accepted.
    """

    if not isinstance(task_name, str) or _TASK_NAME.fullmatch(task_name.strip()) is None:
        raise GuardianOperatorError("task_name is outside the static registration policy")
    config = Path(config_path).resolve()
    data = Path(data_dir).resolve()
    profiles = load_launch_profiles(config)
    command = [
        sys.executable,
        "-m",
        "scripts.devfarm_guardian",
        "serve",
        "--config",
        str(config),
        "--data-dir",
        str(data),
        "--poll-seconds",
        "6",
    ]
    result: dict[str, Any] = {
        "status": "DRY_RUN",
        "registration": "NOT_APPLIED",
        "task_name": task_name.strip(),
        "profile_count": len(profiles),
        "command": command,
        "arbitrary_command": False,
        "os": os.name,
    }
    if not apply:
        return result
    if os.name != "nt":
        raise GuardianOperatorError("Windows Task Scheduler registration requires Windows")
    completed = subprocess.run(
        [
            "schtasks.exe",
            "/Create",
            "/TN",
            task_name.strip(),
            "/SC",
            "ONSTART",
            "/TR",
            subprocess.list2cmdline(command),
            "/F",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise GuardianOperatorError("Windows Task Scheduler registration failed")
    return {
        **result,
        "status": "APPLIED",
        "registration": "CONFIGURED",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Host-owned bounded Guardian operator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    health = subparsers.add_parser("health")
    health.add_argument("--config", required=True, type=Path)
    run = subparsers.add_parser("run")
    run.add_argument("--config", required=True, type=Path)
    run.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DEV_AGENT_DATA_DIR", ".dev_agent")))
    serve = subparsers.add_parser("serve")
    serve.add_argument("--config", required=True, type=Path)
    serve.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DEV_AGENT_DATA_DIR", ".dev_agent")))
    serve.add_argument("--poll-seconds", type=float, default=6.0)
    serve.add_argument("--max-cycles", type=int)
    install = subparsers.add_parser("install", help="show or explicitly apply static OS liveness registration")
    install.add_argument("--config", required=True, type=Path)
    install.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DEV_AGENT_DATA_DIR", ".dev_agent")))
    install.add_argument("--task-name", default="DevAgentGuardian")
    install.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "health":
            result = guardian_health(args.config)
        elif args.command == "run":
            result = guardian_run_once(args.config, args.data_dir)
        elif args.command == "serve":
            result = guardian_serve(
                args.config,
                args.data_dir,
                poll_seconds=args.poll_seconds,
                max_cycles=args.max_cycles,
            )
        else:
            result = guardian_registration(
                args.config,
                args.data_dir,
                task_name=args.task_name,
                apply=args.apply,
            )
    except GuardianOperatorError as exc:
        print(json.dumps({"status": "REJECTED", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "GuardianOperatorError",
    "guardian_health",
    "guardian_registration",
    "guardian_run_once",
    "guardian_serve",
    "load_launch_profiles",
    "main",
]
