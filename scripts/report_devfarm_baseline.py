"""Render an explicit, read-only W0 DevFarm baseline report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

if __package__ in {None, ""}:  # pragma: no cover - exercised by path entrypoint test
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.devfarm_baseline import BaselineError, build_baseline_report
from scripts.devfarm_repository import DevFarmError, read_bounded_json, repository_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build a bounded W0 report from an explicit task/attempt selection. "
            "No provider, plan, routing, or Gate mutation is performed."
        )
    )
    parser.add_argument("selection", help="repository-local JSON selection manifest")
    parser.add_argument("--root", default=".", help="repository root (default: current directory)")
    parser.add_argument("--output", help="repository-local report path; stdout is used when omitted")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = Path(args.root).resolve()
    try:
        selection = read_bounded_json(root, args.selection, maximum_chars=250_000)
        report = build_baseline_report(root, selection)
        rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            target = repository_path(root, args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
        else:
            sys.stdout.write(rendered)
        return 0
    except (BaselineError, DevFarmError, OSError) as exc:
        print(f"baseline report failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

