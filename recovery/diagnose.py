"""Read-only diagnostics that do not import the normal v2 Runtime."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import subprocess
import sys

try:
    from .test_results import validate_junit_report
    from .validate_artifacts import validate_artifact_root
    from .validate_resources import validate_resource_ledger
except ImportError:  # direct ``python recovery/diagnose.py`` execution
    from test_results import validate_junit_report
    from validate_artifacts import validate_artifact_root
    from validate_resources import validate_resource_ledger


@dataclass(frozen=True)
class Diagnostic:
    name: str
    ok: bool
    detail: str


def _git_health(root: Path) -> Diagnostic:
    safe_root = root.as_posix()
    try:
        top_level = subprocess.run(
            ["git", "-c", f"safe.directory={safe_root}", "rev-parse", "--show-toplevel"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if top_level.returncode != 0:
            return Diagnostic("git_health", False, top_level.stderr.strip() or "git metadata is not readable")
        head = subprocess.run(
            ["git", "-c", f"safe.directory={safe_root}", "rev-parse", "--verify", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        status = subprocess.run(
            ["git", "-c", f"safe.directory={safe_root}", "status", "--porcelain=v1", "--branch"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if head.returncode != 0 or status.returncode != 0:
            return Diagnostic("git_health", False, (head.stderr or status.stderr).strip() or "git health check failed")
        dirty = len(status.stdout.splitlines()) > 1
        commit = head.stdout.strip()
        return Diagnostic("git_health", True, f"HEAD={commit}; working_tree={'dirty' if dirty else 'clean'}")
    except (OSError, subprocess.SubprocessError) as exc:
        return Diagnostic("git_health", False, f"git health check unavailable: {exc}")


def run_diagnostics(root: str | Path, *, test_report: str | Path | None = None, artifact_root: str | Path | None = None, resource_ledger: str | Path | None = None) -> list[Diagnostic]:
    """Inspect repository prerequisites without network or provider access."""
    path = Path(root).expanduser().resolve()
    checks = (
        ("repository_root", path.is_dir(), str(path)),
        ("git_metadata", (path / ".git").exists(), str(path / ".git")),
        ("v2_spec", (path / "spec" / "v2").is_dir(), str(path / "spec" / "v2")),
        ("v2_source", (path / "src" / "dev_agent").is_dir(), str(path / "src" / "dev_agent")),
        ("v2_gate_status", (path / "spec" / "v2" / "GATE_STATUS.json").is_file(), str(path / "spec" / "v2" / "GATE_STATUS.json")),
        ("v2_workflow", (path / ".github" / "workflows" / "v2-core.yml").is_file(), str(path / ".github" / "workflows" / "v2-core.yml")),
        ("configuration", all((path / item).is_file() for item in ("config/config.yaml", "config/access.yaml", "config/oi_profile.yaml")), "config files exist; contents not read"),
        ("v2_test_runner", all((path / item).is_file() for item in ("pytest.ini", "requirements-v2-dev.txt")), "pytest.ini and v2 requirements exist"),
    )
    diagnostics = [Diagnostic(name, ok, detail) for name, ok, detail in checks]
    if test_report is not None:
        ok, detail = validate_junit_report(test_report)
        diagnostics.append(Diagnostic("persisted_test_report", ok, detail))
    if artifact_root is not None:
        ok, detail = validate_artifact_root(artifact_root)
        diagnostics.append(Diagnostic("event_artifact_root", ok, detail))
    if resource_ledger is not None:
        ok, detail = validate_resource_ledger(resource_ledger)
        diagnostics.append(Diagnostic("resource_ledger", ok, detail))
    if path.is_dir() and (path / ".git").exists():
        diagnostics.append(_git_health(path))
    return diagnostics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run read-only dev_agent recovery diagnostics")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--test-report", type=Path, help="validate a persisted JUnit XML report")
    parser.add_argument("--artifact-root", type=Path, help="validate an event artifact root")
    parser.add_argument("--resource-ledger", type=Path, help="validate the Phase 6 resource ledger")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    diagnostics = run_diagnostics(args.root, test_report=args.test_report, artifact_root=args.artifact_root, resource_ledger=args.resource_ledger)
    if args.as_json:
        print(json.dumps([asdict(item) for item in diagnostics], ensure_ascii=False, indent=2))
    else:
        for item in diagnostics:
            print(f"{'PASS' if item.ok else 'FAIL'} {item.name}: {item.detail}")
    return 0 if all(item.ok for item in diagnostics) else 1


if __name__ == "__main__":
    sys.exit(main())
