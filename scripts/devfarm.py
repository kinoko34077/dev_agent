"""Small development-only Worker Farm boundary.

This is not a runtime scheduler or a Phase 7 AgentBackend.  It validates
narrow task/result contracts and prepares an isolated Git worktree only for
host verification, so a free model never needs write access to ``v2/bootstrap``
or another worker's checkout.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4
from typing import Any, Mapping

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
from src.dev_agent.security.protected_paths import is_protected_path


_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
_BRANCH = re.compile(r"^agent/[A-Za-z0-9._/-]+$")


def init_farm(root: str | Path) -> Path:
    repository = Path(root).resolve()
    farm = repository / ".devfarm"
    for name in ("tasks", "results", "shared", "status", "worktrees", "plans"):
        (farm / name).mkdir(parents=True, exist_ok=True)
    return farm


def write_manifest(root: str | Path, value: Mapping[str, Any]) -> Path:
    manifest = validate_manifest(value)
    path = init_farm(root) / "tasks" / f"{manifest['task_id']}.json"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_result(root: str | Path, value: Mapping[str, Any], *, manifest: Mapping[str, Any]) -> Path:
    normalized_manifest = validate_manifest(manifest)
    result = validate_result(value, manifest=normalized_manifest)
    directory = init_farm(root) / "results" / normalized_manifest["task_id"]
    directory.mkdir(parents=True, exist_ok=True)
    attempt_id = result.get("attempt_id") or uuid4().hex
    result["attempt_id"] = attempt_id
    attempt_directory = directory / "attempts" / attempt_id
    attempt_directory.mkdir(parents=True, exist_ok=False)
    attempt_path = attempt_directory / "result.json"
    attempt_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="strict")
    path = directory / "result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    history_path = directory / "attempts.jsonl"
    with history_path.open("a", encoding="utf-8", newline="\n") as history:
        history.write(json.dumps({"attempt_id": attempt_id, "status": result["status"], "recorded_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")
    return path


def prepare_worktree(root: str | Path, *, task_id: str, branch: str, revision: str | None = None) -> Path:
    repository = Path(root).resolve()
    if not _TASK_ID.fullmatch(task_id):
        raise DevFarmError("task_id contains unsafe characters")
    if not _BRANCH.fullmatch(branch) or branch.endswith("/"):
        raise DevFarmError("worker branch must use the agent/<provider>/<task> form")
    if revision is not None and (not isinstance(revision, str) or not revision.strip() or revision.lstrip().startswith("-") or any(char in revision for char in "\r\n")):
        raise DevFarmError("revision must be a safe Git revision")
    worktree = init_farm(repository) / "worktrees" / task_id
    if worktree.exists():
        raise FileExistsError(worktree)
    command = ["git", "-c", f"safe.directory={repository.as_posix()}", "worktree", "add", "-b", branch, str(worktree)]
    if revision:
        command.append(revision.strip())
    result = subprocess.run(command, cwd=repository, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git worktree add failed")
    return worktree


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
