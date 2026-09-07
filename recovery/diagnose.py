"""Read-only diagnostics that do not import the normal v2 Runtime."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import sys


@dataclass(frozen=True)
class Diagnostic:
    name: str
    ok: bool
    detail: str


def run_diagnostics(root: str | Path) -> list[Diagnostic]:
    """Inspect repository prerequisites without network or provider access."""
    path = Path(root).expanduser().resolve()
    checks = (
        ("repository_root", path.is_dir(), str(path)),
        ("git_metadata", (path / ".git").exists(), str(path / ".git")),
        ("v2_spec", (path / "spec" / "v2").is_dir(), str(path / "spec" / "v2")),
        ("v2_source", (path / "src" / "dev_agent").is_dir(), str(path / "src" / "dev_agent")),
        ("dev_requirements", (path / "requirements-dev.txt").is_file(), str(path / "requirements-dev.txt")),
    )
    return [Diagnostic(name, ok, detail) for name, ok, detail in checks]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run read-only dev_agent recovery diagnostics")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    diagnostics = run_diagnostics(args.root)
    if args.as_json:
        print(json.dumps([asdict(item) for item in diagnostics], ensure_ascii=False, indent=2))
    else:
        for item in diagnostics:
            print(f"{'PASS' if item.ok else 'FAIL'} {item.name}: {item.detail}")
    return 0 if all(item.ok for item in diagnostics) else 1


if __name__ == "__main__":
    sys.exit(main())
