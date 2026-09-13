"""Print bounded, non-secret reasons why discovered models are or are not usable."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.resources.billing_catalog import profile_for
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog
from src.dev_agent.resources.qualification import QualificationResolver


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
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= 1000:
        raise ValueError("limit must be from 1 to 1000")
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
        row.update(
            {
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / "spec" / "v2" / "model_evidence")
    parser.add_argument("--provider")
    parser.add_argument("--binding")
    parser.add_argument("--model")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        rows = diagnose_entries(
            ModelEvidenceCatalog.load(args.evidence_dir),
            provider_id=args.provider,
            provider_binding_id=args.binding,
            model_id=args.model,
            limit=args.limit,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "category": type(exc).__name__}, ensure_ascii=False))
        return 2
    if args.as_json:
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
