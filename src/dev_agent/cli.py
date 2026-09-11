"""Human-facing command-line boundary for the v2 Operation Layer."""

from __future__ import annotations

import argparse
import json

from .domain.protocol import RiskLevel, Task, TaskType
from .operation import OperationConfig, OperationControl, OperationError, OperationService


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-dir", default=None, help="directory for durable operation state (default: .dev_agent)")
    parser.add_argument("--provider", dest="provider_id", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--binding", dest="provider_binding_id", default=None)
    parser.add_argument("--quota-domain", dest="quota_domain", default=None)
    parser.add_argument("--provider-pool", dest="provider_pool", default=None, help="JSON array of non-secret provider bindings")
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--lease-seconds", type=float, default=None)
    parser.add_argument("--idle-sleep-seconds", type=float, default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dev-agent", description="Operate the dev_agent v2 control plane")
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start", help="start the durable Worker loop")
    _add_common_arguments(start)
    start.add_argument("--once", action="store_true", help="claim at most one task and exit")
    submit = subparsers.add_parser("submit", help="persist and enqueue one Task")
    _add_common_arguments(submit)
    submit.add_argument("objective")
    submit.add_argument("--priority", type=int, default=0)
    submit.add_argument("--sensitivity", choices=("public", "normal", "internal", "sensitive"), default="normal")
    submit.add_argument("--task-type", choices=tuple(item.value for item in TaskType), default=TaskType.REASONING.value)
    submit.add_argument("--risk", choices=tuple(item.value for item in RiskLevel), default=RiskLevel.NORMAL.value)
    status = subparsers.add_parser("status", help="show durable Task state")
    _add_common_arguments(status)
    status.add_argument("task_id")
    stop = subparsers.add_parser("stop", help="request Worker stop, optionally cancel one Task")
    _add_common_arguments(stop)
    stop.add_argument("task_id", nargs="?")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = OperationConfig.from_environment(
            data_dir=args.data_dir,
            provider_id=getattr(args, "provider_id", None),
            model=getattr(args, "model", None),
            provider_binding_id=getattr(args, "provider_binding_id", None),
            quota_domain=getattr(args, "quota_domain", None),
            provider_pool=getattr(args, "provider_pool", None),
            worker_id=getattr(args, "worker_id", None),
            lease_seconds=getattr(args, "lease_seconds", None),
            idle_sleep_seconds=getattr(args, "idle_sleep_seconds", None),
        )
        if args.command == "submit":
            task = OperationService.submit(config, args.objective, priority=args.priority, sensitivity=args.sensitivity, task_type=args.task_type, risk=args.risk)
            print(json.dumps({"task_id": task.task_id, "state": task.status.value}, ensure_ascii=False))
            return 0
        if args.command == "status":
            print(json.dumps(OperationService.read_status(config, args.task_id), ensure_ascii=False))
            return 0
        if args.command == "stop" and args.task_id is None:
            control = OperationControl(config.queue_path)
            try:
                control.request_stop()
            finally:
                control.close()
            print(json.dumps({"stop_requested": True}, ensure_ascii=False))
            return 0
        if args.command == "stop":
            status = OperationService.cancel_task(config, args.task_id)
            print(json.dumps(status, ensure_ascii=False))
            return 0
        with OperationService.open(config) as service:
            if args.command == "start":
                result = service.start(once=args.once)
                if isinstance(result, Task):
                    result = OperationService._status(service.store, service.queue, result.task_id)
                print(json.dumps(result, ensure_ascii=False))
                return 0
    except (OperationError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    return 2
