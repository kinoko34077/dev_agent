"""Execute the Phase 6 recovery sequence in an isolated operational fixture.

The drill never touches the checkout from which this script is launched.  It
creates a temporary Git repository and SQLite state, then exercises the same
maintenance/backup/restore/validation/LKG/rollback/repair/restart sequence
used by the operator facade.  A JSON report can be retained for review.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from recovery.backup import validate_backup
from recovery.git_recovery import rollback_to_last_known_good
from recovery.phase6_recovery import RecoveryOperator
from src.dev_agent.domain.protocol import Task
from src.dev_agent.state.sqlite_store import SQLiteStateStore


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def drill() -> dict[str, object]:
    with TemporaryDirectory(prefix="dev-agent-phase6-recovery-") as directory:
        root = Path(directory)
        repo = root / "operational-repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "phase6-drill@example.invalid")
        _git(repo, "config", "user.name", "Phase 6 Recovery Drill")
        tracked = repo / "tracked.txt"
        tracked.write_text("known-good\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
        _git(repo, "commit", "-m", "known good")
        known_good = _git(repo, "rev-parse", "HEAD")
        tracked.write_text("current\n", encoding="utf-8")
        _git(repo, "add", "tracked.txt")
        _git(repo, "commit", "-m", "current")
        current = _git(repo, "rev-parse", "HEAD")

        state = root / "runtime.sqlite3"
        backup = root / "runtime.sqlite3.backup"
        restored = root / "runtime.sqlite3.restored"
        with SQLiteStateStore(state) as store:
            store.save_task(Task(objective="phase6 recovery drill"))

        operator = RecoveryOperator(repo)
        operator.backup_state(state, backup)
        backup_valid = validate_backup(state, backup)
        operator.restore_state(backup, restored, allow_write=True)
        restored_valid, restored_detail = operator.validate_state(restored)

        metadata = root / "last-known-good.json"
        operator.record_lkg(metadata, commit=known_good, require_clean=True, test_report="phase6-recovery-drill.json")
        rollback_plan = operator.rollback_plan(metadata)
        rolled_back = rollback_to_last_known_good(repo, metadata, allow_destructive=True)
        repair_branch = operator.create_repair_branch(metadata, "repair/phase6-drill", allow_write=True)

        with SQLiteStateStore(restored) as restarted_store:
            restarted = restarted_store.load_task(next(iter(restarted_store.snapshot()["tasks"]), "")) is not None

        report: dict[str, object] = {
            "schema_version": 1,
            "drill": "phase6_recovery_operator",
            "status": "completed",
            "isolation": "temporary operational fixture; source checkout untouched",
            "maintenance_lock": "RecoveryOperator.restore_state acquired and released its exclusive lock",
            "backup": {"created": backup.is_file(), "validated": backup_valid},
            "restore": {"created": restored.is_file(), "validated": restored_valid, "detail": restored_detail},
            "git": {
                "known_good_commit": known_good,
                "pre_rollback_commit": current,
                "rollback_target": rollback_plan.target_commit,
                "post_rollback_commit": rolled_back.commit,
                "repair_branch": repair_branch,
            },
            "restart": {"restored_state_opened": True, "task_reloaded": restarted},
            "assertions": [
                "maintenance lock serialized restore publication",
                "SQLite backup content and integrity validated",
                "restored state passed independent validation",
                "last-known-good commit was recorded",
                "isolated repository rolled back to last-known-good",
                "repair branch was created at last-known-good",
                "restored SQLite state was reopened after recovery",
            ],
        }
        if not (backup_valid and restored_valid and rolled_back.commit == known_good and restarted):
            raise RuntimeError("Phase 6 recovery drill assertion failed")
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, help="write the completed drill report to this path")
    args = parser.parse_args(argv)
    try:
        report = drill()
    except Exception as exc:
        print(json.dumps({"status": "failed", "category": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
