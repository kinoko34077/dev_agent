"""Build operator-reviewed alias/capability candidates from a model snapshot.

This command is intentionally explicit: it never replaces the canonical
evidence files by default.  Discovery metadata is factual input only; the
normal qualification, billing, privacy, quota, and runtime gates remain
unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.refresh_model_catalog import write_candidate
from src.dev_agent.resources.model_catalog import ModelCatalog
from src.dev_agent.resources.model_evidence_builder import build_alias_document, build_capability_document


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"input snapshot is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError("input snapshot must be an object")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        default=ROOT / "spec" / "v2" / "model_evidence" / "model_catalog_snapshot.json",
        type=Path,
    )
    parser.add_argument("--aliases-output", required=True, type=Path)
    parser.add_argument("--capabilities-output", required=True, type=Path)
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args(argv)
    try:
        catalog = ModelCatalog.from_document(_load(args.catalog))
        aliases = build_alias_document(catalog)
        capabilities = build_capability_document(catalog)
        write_candidate(args.aliases_output, aliases, replace_existing=args.replace_existing)
        write_candidate(args.capabilities_output, capabilities, replace_existing=args.replace_existing)
    except Exception as exc:
        print(json.dumps({"status": "failed", "category": type(exc).__name__}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": "candidates_written",
                "alias_count": len(aliases["entries"]),
                "capability_count": len(capabilities["entries"]),
                "aliases_output": str(args.aliases_output),
                "capabilities_output": str(args.capabilities_output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
