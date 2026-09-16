"""Small local entrypoint for the always-on Operation coordinator.

This command keeps one Agent runtime process alive without introducing a
second scheduler.  ``serve`` is intentionally foreground and may be bounded
for dogfood; OS startup registration remains a separate deferred Guardian
track.  Provider credentials and arbitrary commands are never accepted by
this entrypoint.
"""

from __future__ import annotations

import argparse
from threading import Event
import json
from pathlib import Path
import signal
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.domain.protocol import Task
from src.dev_agent.operation import OperationConfig, OperationError, OperationService
from src.dev_agent.operation_runtime import (
    RuntimeCoordinator,
    RuntimeCoordinatorError,
    read_runtime_health,
)


def _add_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--provider", dest="provider_id", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--binding", dest="provider_binding_id", default=None)
    parser.add_argument("--quota-domain", dest="quota_domain", default=None)
    parser.add_argument("--provider-pool", dest="provider_pool", default=None)
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--lease-seconds", type=float, default=None)
    parser.add_argument("--idle-sleep-seconds", type=float, default=None)
    parser.add_argument("--revision", default="working-tree")
    parser.add_argument("--role", default="agent")
    parser.add_argument("--instance-id", default="operation-runtime")
    parser.add_argument("--presence-lease-seconds", type=float, default=60.0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    health = subparsers.add_parser("health", help="read bounded runtime state without opening a Provider")
    health.add_argument("--data-dir", default=None)
    health.add_argument("--role", default="agent")
    health.add_argument("--instance-id", default="operation-runtime")
    once = subparsers.add_parser("once", help="run one existing Operation boundary")
    _add_runtime_arguments(once)
    serve = subparsers.add_parser("serve", help="keep the existing Operation loop alive")
    _add_runtime_arguments(serve)
    serve.add_argument("--max-cycles", type=int, default=None)
    serve.add_argument("--max-runtime-seconds", type=float, default=None)
    return parser


def _operation_config(args: argparse.Namespace) -> OperationConfig:
    return OperationConfig.from_environment(
        data_dir=args.data_dir,
        provider_id=args.provider_id,
        model=args.model,
        provider_binding_id=args.provider_binding_id,
        quota_domain=args.quota_domain,
        provider_pool=args.provider_pool,
        worker_id=args.worker_id,
        lease_seconds=args.lease_seconds,
        idle_sleep_seconds=args.idle_sleep_seconds,
    )


def _install_signal_handlers(stop_event: Event) -> dict[int, object]:
    previous: dict[int, object] = {}

    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    for name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        except (OSError, RuntimeError, ValueError):
            continue
    return previous


def _restore_signal_handlers(previous: dict[int, object]) -> None:
    for signum, handler in previous.items():
        try:
            signal.signal(signum, handler)
        except (OSError, RuntimeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "health":
            data_dir = args.data_dir or ".dev_agent"
            print(
                json.dumps(
                    read_runtime_health(
                        data_dir,
                        role=args.role,
                        instance_id=args.instance_id,
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        config = _operation_config(args)
        with RuntimeCoordinator.open(
            config,
            revision=args.revision,
            role=args.role,
            instance_id=args.instance_id,
            presence_lease_seconds=args.presence_lease_seconds,
        ) as runtime:
            if args.command == "once":
                result = runtime.run_once()
                if isinstance(result, Task):
                    result = OperationService._status(runtime.operation.store, runtime.operation.queue, result.task_id)
                print(json.dumps(result, ensure_ascii=False, sort_keys=True))
                return 0

            stop_event = Event()
            previous = _install_signal_handlers(stop_event)
            try:
                result = runtime.serve(
                    stop_event=stop_event,
                    max_cycles=args.max_cycles,
                    max_runtime_seconds=args.max_runtime_seconds,
                )
            finally:
                _restore_signal_handlers(previous)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
    except (OperationError, RuntimeCoordinatorError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
