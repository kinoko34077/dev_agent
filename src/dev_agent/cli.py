"""Human-facing command-line boundary for the v2 Operation Layer."""

from __future__ import annotations

import argparse
import json

from .domain.protocol import RiskLevel, Task, TaskType
from .operation import OperationConfig, OperationControl, OperationError, OperationService
from .resources.ledger import ResourceLedger
from .resources.qualification import QualificationResolver
from .resources.repair import apply_resource_repairs, plan_resource_repairs


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
    resource = subparsers.add_parser("resource", help="validate or explicitly repair Resource projections")
    resource_subparsers = resource.add_subparsers(dest="resource_command", required=True)
    for name, help_text in (
        ("validate", "inspect legacy Resource projections without mutation"),
        ("migrate", "plan or explicitly apply Resource projection repairs"),
    ):
        resource_command = resource_subparsers.add_parser(name, help=help_text)
        resource_command.add_argument("--data-dir", default=None)
        resource_command.add_argument("--resource-id", action="append", dest="resource_ids", default=None)
    resource_subparsers.choices["migrate"].add_argument(
        "--apply",
        action="store_true",
        help="apply repair plans; without this flag the command is a dry run",
    )
    resource_subparsers.choices["migrate"].add_argument(
        "--operator-ref",
        default=None,
        help="required audit identity when --apply is used",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "resource":
            config = OperationConfig.from_environment(data_dir=args.data_dir)
            resource_ids = set(args.resource_ids or ()) or None
            resolver = QualificationResolver()
            with ResourceLedger(config.resources_path) as ledger:
                if args.resource_command == "validate":
                    plans = plan_resource_repairs(ledger, resolver, resource_ids=resource_ids)
                    payload = [plan.to_dict() for plan in plans]
                    print(json.dumps({"resources": payload}, ensure_ascii=False))
                    return 1 if any(plan["status"] == "blocked" for plan in payload) else 0
                if args.apply and not isinstance(args.operator_ref, str) or args.apply and not args.operator_ref.strip():
                    parser.error("resource migrate --apply requires --operator-ref")
                if args.apply:
                    plans = apply_resource_repairs(
                        ledger,
                        resolver,
                        operator_ref=args.operator_ref,
                        resource_ids=resource_ids,
                    )
                else:
                    plans = plan_resource_repairs(ledger, resolver, resource_ids=resource_ids)
                print(json.dumps({"applied": bool(args.apply), "resources": [plan.to_dict() for plan in plans]}, ensure_ascii=False))
                return 0 if not any(plan.status == "blocked" for plan in plans) else 1
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
