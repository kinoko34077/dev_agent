"""Refresh a bounded candidate benchmark snapshot from OpenRouter's API.

Only exact canonical IDs or uniquely date-suffixed variants already present in
the reviewed model catalog are accepted.  Unknown and ambiguous model names
are omitted rather than guessed.  The output is a create-only candidate until
an operator explicitly reviews and replaces the canonical snapshot.
"""

from __future__ import annotations

import argparse
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping
from urllib.request import Request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.refresh_model_catalog import write_candidate
from src.dev_agent.providers.benchmark_discovery import benchmark_scores_from_document
from src.dev_agent.providers.openai_compatible.http import _read_bounded, urlopen_no_redirect
from src.dev_agent.resources.model_catalog import ModelCatalog
from src.dev_agent.resources.model_evidence_builder import canonical_model_id_for


_MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def _load(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"snapshot is unreadable: {path.name}") from exc
    if not isinstance(value, Mapping):
        raise ValueError("snapshot must be an object")
    return value


def _fetch(api_key: str, *, timeout_seconds: float) -> Mapping[str, Any]:
    request = Request(
        "https://openrouter.ai/api/v1/benchmarks",
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    with urlopen_no_redirect(request, timeout=timeout_seconds) as response:
        raw = _read_bounded(response, _MAX_RESPONSE_BYTES)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("benchmark response is not valid JSON") from exc
    if not isinstance(value, Mapping):
        raise ValueError("benchmark response must be an object")
    return value


def refresh(
    catalog: ModelCatalog,
    *,
    document: Mapping[str, Any],
    observed_at=None,
    confidence: str = "medium",
) -> dict[str, Any]:
    canonical_ids = {
        canonical_model_id_for(entry.provider_id, entry.model_id)
        for entry in catalog.entries(now=observed_at)
        if entry.is_text_generation_candidate()
    }
    rows = benchmark_scores_from_document(
        document,
        canonical_model_ids=canonical_ids,
        observed_at=observed_at,
        ttl=timedelta(days=7),
        confidence=confidence,
    )
    return {
        "schema_version": 1,
        "tier_thresholds": {"L1": 0, "L2": 30, "L3": 60},
        "entries": sorted(rows, key=lambda row: (row["canonical_model_id"], row["benchmark"], row["source"])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalog",
        default=ROOT / "spec" / "v2" / "model_evidence" / "model_catalog_snapshot.json",
        type=Path,
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--confidence", choices=("low", "medium", "high"), default="medium")
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args(argv)
    try:
        api_key = os.environ.get(args.api_key_env)
        if not isinstance(api_key, str) or not api_key:
            raise ValueError("benchmark API key is not configured")
        catalog = ModelCatalog.from_document(_load(args.catalog))
        document = refresh(catalog, document=_fetch(api_key, timeout_seconds=args.timeout_seconds))
        write_candidate(args.output, document, replace_existing=args.replace_existing)
    except Exception as exc:
        print(json.dumps({"status": "failed", "category": type(exc).__name__}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "candidate_written", "entry_count": len(document["entries"]), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
