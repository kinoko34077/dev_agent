"""Compare two explicit observed DevFarm baseline reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

if __package__ in {None, ""}:  # pragma: no cover - exercised by path entrypoint
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.devfarm_baseline import BaselineError, build_comparison_report
from scripts.devfarm_repository import DevFarmError, read_bounded_json, repository_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two observed DevFarm reports. Unmatched or synthetic "
            "evidence remains insufficient and is never promoted."
        )
    )
    parser.add_argument("baseline", help="repository-local observed baseline report")
    parser.add_argument("treatment", help="repository-local observed treatment report")
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--output", help="repository-local comparison path; stdout when omitted")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    try:
        baseline = read_bounded_json(root, args.baseline, maximum_chars=500_000)
        treatment = read_bounded_json(root, args.treatment, maximum_chars=500_000)
        report = build_comparison_report(baseline, treatment)
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            target = repository_path(root, args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except (BaselineError, DevFarmError, OSError) as exc:
        print(f"baseline comparison failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

