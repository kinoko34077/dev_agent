"""Verify that CI is testing the exact checked-out commit."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-c", f"safe.directory={root.as_posix()}", *args], cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def check(root: str | Path, expected: str | None = None) -> tuple[bool, str]:
    path = Path(root).resolve()
    actual = _git(path, "rev-parse", "--verify", "HEAD")
    if expected and actual != expected:
        return False, f"checked-out HEAD {actual} does not match expected {expected}"
    dirty = _git(path, "status", "--porcelain=v1")
    if dirty:
        return False, "worktree is dirty"
    return True, f"HEAD={actual}; worktree=clean"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--expected")
    args = parser.parse_args(argv)
    try:
        ok, message = check(args.root, args.expected)
    except (OSError, RuntimeError) as exc:
        print(f"HEAD_CHECK_ERROR: {exc}")
        return 1
    print(f"{'PASS' if ok else 'FAIL'}: {message}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
