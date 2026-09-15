"""Small development-only Worker Farm boundary.

This is not a runtime scheduler or a Phase 7 AgentBackend.  It validates
narrow task/result contracts and prepares an isolated Git worktree only for
host verification, so a free model never needs write access to ``v2/bootstrap``
or another worker's checkout.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.devfarm_errors import DevFarmError
from scripts.devfarm_contracts import (
    MAX_ACCEPTANCE,
    MAX_ACCEPTANCE_CHARS,
    MAX_OBJECTIVE_CHARS,
    MAX_OUTBOUND_BYTES,
    MAX_OUTBOUND_FILES,
    MAX_REQUIREMENT_CHARS,
    MAX_REQUIREMENTS,
    MAX_TEST_COMMANDS,
    VERIFICATION_TRUST_LEVELS,
    canonical_digest,
    normalize_patch_hunk_counts,
    parse_host_test_command,
    sha256_text,
    validate_manifest,
    validate_patch,
    validate_result,
)
from scripts.devfarm_repository import read_json
from scripts.devfarm_workspace import init_farm, prepare_worktree, write_manifest, write_result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--root", type=Path, default=Path.cwd())
    worktree = sub.add_parser("prepare-worktree")
    worktree.add_argument("task_id")
    worktree.add_argument("branch")
    worktree.add_argument("--revision")
    worktree.add_argument("--root", type=Path, default=Path.cwd())
    manifest = sub.add_parser("validate-manifest")
    manifest.add_argument("path", type=Path)
    result = sub.add_parser("validate-result")
    result.add_argument("path", type=Path)
    result.add_argument("--manifest", required=True, type=Path)
    plan = sub.add_parser("plan", help="create a durable development parent plan")
    plan.add_argument("spec", type=Path)
    plan.add_argument("--root", type=Path, default=Path.cwd())
    dispatch = sub.add_parser("dispatch", help="dispatch READY worker tasks as remote proposals")
    dispatch.add_argument("run_id")
    dispatch.add_argument("--provider")
    dispatch.add_argument("--model")
    dispatch.add_argument("--timeout-seconds", type=float, default=30.0)
    dispatch.add_argument(
        "--execution-boundary",
        choices=("host_process", "in_process"),
        default="host_process",
        help="where the concrete Worker Provider call runs; live operation defaults to the Host process",
    )
    dispatch.add_argument("--root", type=Path, default=Path.cwd())
    status = sub.add_parser("status", help="show a durable parent plan")
    status.add_argument("run_id")
    status.add_argument("--root", type=Path, default=Path.cwd())
    collect = sub.add_parser("collect", help="collect existing Worker result artifacts")
    collect.add_argument("run_id")
    collect.add_argument("--root", type=Path, default=Path.cwd())
    verify = sub.add_parser("verify", help="host-verify proposed Worker results")
    verify.add_argument("run_id")
    verify.add_argument("--task-id", action="append", dest="task_ids")
    verify.add_argument("--trust-level", choices=sorted(VERIFICATION_TRUST_LEVELS), default="STATIC_ONLY")
    verify.add_argument("--operator-approved", action="store_true")
    verify.add_argument("--root", type=Path, default=Path.cwd())
    resume = sub.add_parser("resume", help="reconcile artifacts and release dependency-ready tasks")
    resume.add_argument("run_id")
    resume.add_argument("--root", type=Path, default=Path.cwd())
    supersede = sub.add_parser("supersede", help="explicitly close a terminal plan and release ownership")
    supersede.add_argument("run_id")
    supersede.add_argument("--reason", required=True)
    supersede.add_argument("--root", type=Path, default=Path.cwd())
    reassign = sub.add_parser("reassign", help="reassign a bounded failed Worker task")
    reassign.add_argument("run_id")
    reassign.add_argument("task_id")
    reassign.add_argument("--provider", required=True)
    reassign.add_argument("--model", required=True)
    reassign.add_argument("--provider-binding-id")
    reassign.add_argument("--root", type=Path, default=Path.cwd())
    integrated = sub.add_parser("mark-integrated", help="record explicit Codex integration review")
    integrated.add_argument("run_id")
    integrated.add_argument("task_id")
    integrated.add_argument("--note", required=True)
    integrated.add_argument("--target-ref", required=True)
    integrated.add_argument("--integration-revision", required=True)
    integrated.add_argument("--source-attempt-id", required=True)
    integrated.add_argument("--verified-patch-digest", required=True)
    integrated.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(init_farm(args.root))
        elif args.command == "prepare-worktree":
            print(prepare_worktree(args.root, task_id=args.task_id, branch=args.branch, revision=args.revision))
        elif args.command == "validate-manifest":
            print(json.dumps(validate_manifest(read_json(args.path)), ensure_ascii=False, indent=2))
        elif args.command == "validate-result":
            normalized_manifest = validate_manifest(read_json(args.manifest))
            normalized_result = validate_result(read_json(args.path), manifest=normalized_manifest)
            print(json.dumps(normalized_result, ensure_ascii=False, indent=2))
        elif args.command == "plan":
            from scripts.devfarm_commander import create_plan

            print(json.dumps(create_plan(args.root, read_json(args.spec)), ensure_ascii=False, indent=2))
        elif args.command == "dispatch":
            from scripts.devfarm_commander import dispatch_cli

            print(
                json.dumps(
                    dispatch_cli(
                        args.root,
                        args.run_id,
                        provider_id=args.provider,
                        model_id=args.model,
                        timeout_seconds=args.timeout_seconds,
                        execution_boundary=args.execution_boundary,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "status":
            from scripts.devfarm_commander import CommanderPlanStore

            print(json.dumps(CommanderPlanStore(args.root).load(args.run_id), ensure_ascii=False, indent=2))
        elif args.command == "collect":
            from scripts.devfarm_commander import collect_plan

            print(json.dumps(collect_plan(args.root, args.run_id), ensure_ascii=False, indent=2))
        elif args.command == "verify":
            from scripts.devfarm_commander import verify_plan

            print(
                json.dumps(
                    verify_plan(
                        args.root,
                        args.run_id,
                        task_ids=args.task_ids,
                        verification_trust_level=args.trust_level,
                        operator_approved=args.operator_approved,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "resume":
            from scripts.devfarm_commander import resume_plan

            print(json.dumps(resume_plan(args.root, args.run_id), ensure_ascii=False, indent=2))
        elif args.command == "supersede":
            from scripts.devfarm_commander import supersede_plan

            print(
                json.dumps(
                    supersede_plan(args.root, args.run_id, reason=args.reason),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "reassign":
            from scripts.devfarm_commander import reassign_task

            print(
                json.dumps(
                    reassign_task(
                        args.root,
                        args.run_id,
                        args.task_id,
                        provider_id=args.provider,
                        model_id=args.model,
                        provider_binding_id=args.provider_binding_id,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "mark-integrated":
            from scripts.devfarm_commander import mark_integrated

            print(
                json.dumps(
                    mark_integrated(
                        args.root,
                        args.run_id,
                        args.task_id,
                        note=args.note,
                        target_ref=args.target_ref,
                        integration_revision=args.integration_revision,
                        source_attempt_id=args.source_attempt_id,
                        verified_patch_digest=args.verified_patch_digest,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            parser.error(f"unsupported command: {args.command}")
    except (DevFarmError, FileExistsError, RuntimeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
