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

_DEFAULT_GUARDIAN_CONFIG = (ROOT / ".dev_agent" / "guardian.json").resolve()
_DEFAULT_GUARDIAN_DATA_DIR = (ROOT / ".dev_agent").resolve()
_TASK_SCHEDULER_RUN_LIMIT = 261

from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_repository import read_json
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
_TASK_NOT_FOUND = re.compile(
    r"(?:cannot find|does not exist|not found|system cannot find)",
    re.IGNORECASE,
)


def _classify_registration_failure(returncode: int, output: str) -> str:
    """Return a bounded scheduler failure category without exposing output."""

    bounded_output = output[:4096].lower() if isinstance(output, str) else ""
    if returncode in {5, -2147024891} or "access is denied" in bounded_output:
        return "access_denied"
    if "261 character" in bounded_output or "too long" in bounded_output:
        return "command_too_long"
    if "not recognized" in bounded_output or "cannot find" in bounded_output:
        return "scheduler_unavailable"
    return "registration_failed"


def _validated_task_name(task_name: str) -> str:
    if not isinstance(task_name, str) or _TASK_NAME.fullmatch(task_name.strip()) is None:
        raise GuardianOperatorError("task_name is outside the static registration policy")
    return task_name.strip()


def load_launch_profiles(path: str | Path) -> tuple[LaunchProfile, ...]:
    """Load only Host-authored static launch profiles from JSON."""

    try:
        document = read_json(Path(path).resolve())
    except DevFarmError as exc:
        raise GuardianOperatorError("Guardian configuration is unreadable") from exc
    if not isinstance(document, dict):
        raise GuardianOperatorError("Guardian configuration must be an object")
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

    task_name = _validated_task_name(task_name)
    config = Path(config_path).resolve()
    data = Path(data_dir).resolve()
    profiles = load_launch_profiles(config)
    script_path = Path(__file__).resolve()
    # Task Scheduler limits the /TR command line to 261 characters.  Keep
    # the normal repository-local registration independent of the Scheduler's
    # working directory by resolving these defaults from this module at
    # runtime, while retaining explicit paths for operator-supplied profiles.
    command = [sys.executable, str(script_path), "serve"]
    if config != _DEFAULT_GUARDIAN_CONFIG:
        command.extend(("--config", str(config)))
    if data != _DEFAULT_GUARDIAN_DATA_DIR:
        command.extend(("--data-dir", str(data)))
    command.extend(("--poll-seconds", "6"))
    command_length = len(subprocess.list2cmdline(command))
    if command_length > _TASK_SCHEDULER_RUN_LIMIT:
        raise GuardianOperatorError(
            "Guardian Task Scheduler command exceeds the 261-character /TR limit"
        )
    result: dict[str, Any] = {
        "status": "DRY_RUN",
        "registration": "NOT_APPLIED",
        "task_name": task_name,
        "profile_count": len(profiles),
        "command": command,
        "command_length": command_length,
        "command_length_limit": _TASK_SCHEDULER_RUN_LIMIT,
        "recovery": {
            "disable_command": ["schtasks.exe", "/Change", "/TN", task_name, "/DISABLE"],
            "unregister_command": ["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
            "arbitrary_command": False,
        },
        "arbitrary_command": False,
        "os": os.name,
    }
    if not apply:
        return result
    if os.name != "nt":
        raise GuardianOperatorError("Windows Task Scheduler registration requires Windows")
    try:
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
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise GuardianOperatorError("Windows Task Scheduler registration timed out") from exc
    except OSError as exc:
        raise GuardianOperatorError("Windows Task Scheduler registration could not start") from exc
    if completed.returncode != 0:
        output = "\n".join(
            value for value in (completed.stdout, completed.stderr) if isinstance(value, str)
        )
        reason = _classify_registration_failure(completed.returncode, output)
        raise GuardianOperatorError(f"Windows Task Scheduler registration failed ({reason})")
    return {
        **result,
        "status": "APPLIED",
        "registration": "CONFIGURED",
    }


def guardian_unregistration(
    *,
    task_name: str = "DevAgentGuardian",
    apply: bool = False,
) -> dict[str, Any]:
    """Build or explicitly apply the reversible static Task Scheduler removal."""

    task_name = _validated_task_name(task_name)
    command = ["schtasks.exe", "/Delete", "/TN", task_name, "/F"]
    result: dict[str, Any] = {
        "status": "DRY_RUN",
        "registration": "UNREGISTER_NOT_APPLIED",
        "task_name": task_name,
        "command": command,
        "arbitrary_command": False,
        "mutation_performed": False,
        "os": os.name,
    }
    if not apply:
        return result
    if os.name != "nt":
        raise GuardianOperatorError("Windows Task Scheduler unregistration requires Windows")
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired as exc:
        raise GuardianOperatorError("Windows Task Scheduler unregistration timed out") from exc
    except OSError as exc:
        raise GuardianOperatorError("Windows Task Scheduler unregistration could not start") from exc
    if completed.returncode != 0:
        raise GuardianOperatorError("Windows Task Scheduler unregistration failed")
    return {
        **result,
        "status": "APPLIED",
        "registration": "UNREGISTERED",
        "mutation_performed": True,
    }


def guardian_os_registration_status(
    config_path: str | Path | None = None,
    *,
    task_name: str = "DevAgentGuardian",
) -> dict[str, Any]:
    """Read the static Windows liveness registration without mutating the OS.

    Only the bounded result category is exposed.  Task Scheduler stdout and
    stderr can contain operator-specific paths or other environment details,
    so neither is returned, logged, or written to evidence.
    """

    task_name = _validated_task_name(task_name)
    query = ["schtasks.exe", "/Query", "/TN", task_name, "/FO", "LIST"]
    profile_count: int | None = None
    config_status = "NOT_PROVIDED"
    if config_path is not None:
        try:
            profile_count = len(load_launch_profiles(config_path))
        except GuardianOperatorError:
            return {
                "status": "NOT_VERIFIED",
                "registration": "NOT_VERIFIED",
                "task_name": task_name,
                "profile_count": None,
                "config_status": "INVALID",
                "query_command_static": True,
                "query_performed": False,
                "mutation_performed": False,
                "arbitrary_command": False,
                "os": os.name,
                "reason": "config_invalid",
            }
        config_status = "VALIDATED"
    result: dict[str, Any] = {
        "status": "NOT_VERIFIED",
        "registration": "NOT_VERIFIED",
        "task_name": task_name,
        "profile_count": profile_count,
        "config_status": config_status,
        "query_command_static": True,
        "query_performed": False,
        "mutation_performed": False,
        "arbitrary_command": False,
        "os": os.name,
    }
    if os.name != "nt":
        return {
            **result,
            "status": "NOT_APPLICABLE",
            "reason": "windows_task_scheduler_only",
        }

    try:
        completed = subprocess.run(
            query,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return {**result, "query_performed": True, "reason": "query_timeout"}
    except OSError:
        return {**result, "query_performed": True, "reason": "query_unavailable"}

    # Classification is intentionally based on a bounded local probe.  Raw
    # provider/OS output is never copied into the projection.
    output = "\n".join(
        value for value in (completed.stdout, completed.stderr) if isinstance(value, str)
    )
    if completed.returncode == 0:
        return {
            **result,
            "status": "CONFIGURED",
            "registration": "CONFIGURED",
            "query_performed": True,
            "observed_task_state": "present",
        }
    if _TASK_NOT_FOUND.search(output):
        return {
            **result,
            "status": "NOT_CONFIGURED",
            "registration": "NOT_CONFIGURED",
            "query_performed": True,
            "observed_task_state": "absent",
        }
    return {
        **result,
        "query_performed": True,
        "reason": "query_failed",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Host-owned bounded Guardian operator")
    subparsers = parser.add_subparsers(dest="command", required=True)
    health = subparsers.add_parser("health")
    health.add_argument("--config", type=Path, default=_DEFAULT_GUARDIAN_CONFIG)
    run = subparsers.add_parser("run")
    run.add_argument("--config", type=Path, default=_DEFAULT_GUARDIAN_CONFIG)
    run.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DEV_AGENT_DATA_DIR", _DEFAULT_GUARDIAN_DATA_DIR)))
    serve = subparsers.add_parser("serve")
    serve.add_argument("--config", type=Path, default=_DEFAULT_GUARDIAN_CONFIG)
    serve.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DEV_AGENT_DATA_DIR", _DEFAULT_GUARDIAN_DATA_DIR)))
    serve.add_argument("--poll-seconds", type=float, default=6.0)
    serve.add_argument("--max-cycles", type=int)
    install = subparsers.add_parser("install", help="show or explicitly apply static OS liveness registration")
    install.add_argument("--config", type=Path, default=_DEFAULT_GUARDIAN_CONFIG)
    install.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DEV_AGENT_DATA_DIR", _DEFAULT_GUARDIAN_DATA_DIR)))
    install.add_argument("--task-name", default="DevAgentGuardian")
    install.add_argument("--apply", action="store_true")
    uninstall = subparsers.add_parser("uninstall", help="show or explicitly remove static OS liveness registration")
    uninstall.add_argument("--task-name", default="DevAgentGuardian")
    uninstall.add_argument("--apply", action="store_true")
    status = subparsers.add_parser("os-status", help="read static Windows liveness registration status")
    status.add_argument("--config", type=Path)
    status.add_argument("--task-name", default="DevAgentGuardian")
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
        elif args.command == "install":
            result = guardian_registration(
                args.config,
                args.data_dir,
                task_name=args.task_name,
                apply=args.apply,
            )
        elif args.command == "uninstall":
            result = guardian_unregistration(task_name=args.task_name, apply=args.apply)
        else:
            result = guardian_os_registration_status(args.config, task_name=args.task_name)
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
    "guardian_os_registration_status",
    "guardian_registration",
    "guardian_unregistration",
    "guardian_run_once",
    "guardian_serve",
    "load_launch_profiles",
    "main",
]
