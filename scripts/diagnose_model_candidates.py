"""Print bounded, non-secret reasons why discovered models are or are not usable."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.resources.billing_catalog import profile_for
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog
from src.dev_agent.resources.qualification import QualificationResolver


MAX_DIAGNOSTIC_ROWS = 5000


def diagnose_entries(
    evidence: ModelEvidenceCatalog,
    *,
    qualification_resolver: QualificationResolver | None = None,
    provider_id: str | None = None,
    provider_binding_id: str | None = None,
    model_id: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Compose static evidence with exact billing/qualification facts."""

    if not isinstance(evidence, ModelEvidenceCatalog):
        raise TypeError("evidence must be a ModelEvidenceCatalog")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= MAX_DIAGNOSTIC_ROWS:
        raise ValueError(f"limit must be from 1 to {MAX_DIAGNOSTIC_ROWS}")
    resolver = qualification_resolver or QualificationResolver()
    rows: list[dict[str, Any]] = []
    for entry in evidence.catalog.entries():
        if provider_id is not None and entry.provider_id != provider_id:
            continue
        if provider_binding_id is not None and entry.provider_binding_id != provider_binding_id:
            continue
        if model_id is not None and entry.model_id != model_id:
            continue
        diagnostic = evidence.resolver.diagnose(entry.provider_id, entry.provider_binding_id, entry.model_id)
        billing = profile_for(entry.provider_id, entry.provider_binding_id, entry.model_id)
        qualification = resolver.resolve(
            entry.provider_id,
            entry.provider_binding_id,
            entry.model_id,
            min_confidence="high",
        )
        result = diagnostic.result
        if result == "ELIGIBLE" and billing is None:
            result = "BILLING_UNKNOWN"
        elif result == "ELIGIBLE" and qualification is None:
            result = "QUALIFICATION_MISSING"
        elif result == "ELIGIBLE":
            result = "ELIGIBLE"
        row = diagnostic.to_dict()
        gate_reason = None
        if result != "ELIGIBLE":
            if row.get("discovery") == "FAIL" or row.get("discovery") == "MISSING":
                gate_reason = "discovery_blocked"
            elif row.get("benchmark") == "FAIL" or row.get("benchmark") == "MISSING":
                gate_reason = "benchmark_blocked"
            elif row.get("capability") == "FAIL" or row.get("capability") == "MISSING":
                gate_reason = "capability_blocked"
            elif billing is None or row.get("billing") == "MISSING":
                gate_reason = "billing_missing"
            elif qualification is None or row.get("qualification") == "MISSING":
                gate_reason = "qualification_missing"
            else:
                gate_reason = str(result).lower()
        row.update(
            {
                "gate_reason": gate_reason,
                "billing": "PASS" if billing is not None else "MISSING",
                "qualification": "PASS" if qualification is not None else "MISSING",
                # Static snapshots do not own live health/quota.  Do not imply
                # that a model is healthy merely because it was discovered.
                "runtime": "UNKNOWN",
                "result": result,
            }
        )
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def summarize_entries(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return bounded aggregate counts without echoing model payloads."""

    result_counts: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    row_count = 0
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("diagnostic rows must be objects")
        row_count += 1
        provider = row.get("provider_id")
        result = row.get("result")
        provider_counts[str(provider).strip() if provider is not None else "UNKNOWN"] += 1
        result_counts[str(result).strip() if result is not None else "UNKNOWN"] += 1
    return {
        "row_count": row_count,
        "eligible_count": result_counts.get("ELIGIBLE", 0),
        "result_counts": dict(sorted(result_counts.items())),
        "provider_counts": dict(sorted(provider_counts.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / "spec" / "v2" / "model_evidence")
    parser.add_argument("--provider")
    parser.add_argument("--binding")
    parser.add_argument("--model")
    limit_group = parser.add_mutually_exclusive_group()
    limit_group.add_argument("--limit", type=int, default=200)
    limit_group.add_argument(
        "--all",
        action="store_true",
        help=f"inspect the bounded full inventory (up to {MAX_DIAGNOSTIC_ROWS} rows)",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="print aggregate gate counts instead of individual model rows",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        limit = MAX_DIAGNOSTIC_ROWS if args.all else args.limit
        rows = diagnose_entries(
            ModelEvidenceCatalog.load(args.evidence_dir),
            provider_id=args.provider,
            provider_binding_id=args.binding,
            model_id=args.model,
            limit=limit,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "category": type(exc).__name__}, ensure_ascii=False))
        return 2
    if args.summary:
        summary = summarize_entries(rows)
        if args.as_json:
            print(json.dumps({"status": "ok", "summary": summary}, ensure_ascii=False, indent=2))
        else:
            print(f"rows\t{summary['row_count']}")
            print(f"eligible\t{summary['eligible_count']}")
            print("result_counts\t" + json.dumps(summary["result_counts"], ensure_ascii=False, sort_keys=True))
            print("provider_counts\t" + json.dumps(summary["provider_counts"], ensure_ascii=False, sort_keys=True))
    elif args.as_json:
        print(json.dumps({"status": "ok", "rows": rows}, ensure_ascii=False, indent=2))
    else:
        headers = ("provider", "binding", "model", "discovery", "benchmark", "capability", "billing", "qualification", "result")
        print("\t".join(headers))
        for row in rows:
            print(
                "\t".join(
                    str(row.get(key, "-"))
                    for key in ("provider_id", "provider_binding_id", "model_id", "discovery", "benchmark", "capability", "billing", "qualification", "result")
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
