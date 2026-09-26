"""Print bounded, non-secret reasons why discovered models are or are not usable."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.resources.billing_catalog import profile_for
from src.dev_agent.resources.model_evidence import ModelEvidenceCatalog
from src.dev_agent.resources.model_runtime import (
    RUNTIME_ELIGIBLE,
    RUNTIME_NOT_PROBED,
    RUNTIME_UNAVAILABLE,
    RUNTIME_UNKNOWN,
    RuntimeAdmissionSnapshot,
)
from src.dev_agent.resources.qualification import QualificationResolver


MAX_DIAGNOSTIC_ROWS = 5000


def diagnose_entries(
    evidence: ModelEvidenceCatalog,
    *,
    qualification_resolver: QualificationResolver | None = None,
    runtime_snapshot: RuntimeAdmissionSnapshot | None = None,
    provider_id: str | None = None,
    provider_binding_id: str | None = None,
    model_id: str | None = None,
    limit: int = 200,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Compose static evidence with exact billing/qualification/runtime facts.

    Runtime state is deliberately *not* inferred.  Without an explicit
    snapshot produced by an existing runtime authority, a statically admitted
    row is ``RUNTIME_NOT_PROBED`` rather than ``RUNTIME_UNAVAILABLE``.
    """

    if not isinstance(evidence, ModelEvidenceCatalog):
        raise TypeError("evidence must be a ModelEvidenceCatalog")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 0 < limit <= MAX_DIAGNOSTIC_ROWS:
        raise ValueError(f"limit must be from 1 to {MAX_DIAGNOSTIC_ROWS}")
    resolver = qualification_resolver or QualificationResolver()
    runtime = runtime_snapshot or RuntimeAdmissionSnapshot.empty()
    rows: list[dict[str, Any]] = []
    for entry in evidence.catalog.entries(now=now):
        if provider_id is not None and entry.provider_id != provider_id:
            continue
        if provider_binding_id is not None and entry.provider_binding_id != provider_binding_id:
            continue
        if model_id is not None and entry.model_id != model_id:
            continue
        diagnostic = evidence.resolver.diagnose(entry.provider_id, entry.provider_binding_id, entry.model_id, now=now)
        billing = profile_for(entry.provider_id, entry.provider_binding_id, entry.model_id)
        qualification = resolver.resolve(
            entry.provider_id,
            entry.provider_binding_id,
            entry.model_id,
            min_confidence="high",
            now=now,
        )
        static_result = diagnostic.result
        result = static_result
        if result == "ELIGIBLE" and billing is None:
            result = "BILLING_UNKNOWN"
        elif result == "ELIGIBLE" and qualification is None:
            result = "QUALIFICATION_MISSING"
        elif result == "ELIGIBLE":
            observation = runtime.lookup(entry.provider_id, entry.provider_binding_id, entry.model_id)
            result = observation.status if observation is not None else RUNTIME_NOT_PROBED
        row = diagnostic.to_dict()
        gate_reason = None
        if result not in {"ELIGIBLE", RUNTIME_ELIGIBLE}:
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
            elif result == RUNTIME_NOT_PROBED:
                gate_reason = "runtime_not_probed"
            elif result == RUNTIME_UNKNOWN:
                gate_reason = "runtime_unknown"
            elif result == RUNTIME_UNAVAILABLE:
                gate_reason = "runtime_unavailable"
            else:
                gate_reason = str(result).lower()
        row.update(
            {
                "gate_reason": gate_reason,
                "static_result": static_result,
                "billing": "PASS" if billing is not None else "MISSING",
                "qualification": "PASS" if qualification is not None else "MISSING",
                # Static snapshots do not own live health/quota.  Do not imply
                # that a model is healthy merely because it was discovered.
                "runtime": result if result in {RUNTIME_ELIGIBLE, RUNTIME_UNAVAILABLE, RUNTIME_UNKNOWN, RUNTIME_NOT_PROBED} else "NOT_REACHED",
                "result": result,
            }
        )
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


def summarize_entries(
    rows: Iterable[Mapping[str, Any]],
    *,
    coverage: Mapping[str, Any] | None = None,
    candidate_promotion: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return bounded aggregate counts without echoing model payloads."""

    result_counts: Counter[str] = Counter()
    static_result_counts: Counter[str] = Counter()
    provider_counts: Counter[str] = Counter()
    row_count = 0
    for row in rows:
        if not isinstance(row, Mapping):
            raise TypeError("diagnostic rows must be objects")
        row_count += 1
        provider = row.get("provider_id")
        result = row.get("result")
        static_result = row.get("static_result")
        provider_counts[str(provider).strip() if provider is not None else "UNKNOWN"] += 1
        result_counts[str(result).strip() if result is not None else "UNKNOWN"] += 1
        if static_result is not None:
            static_result_counts[str(static_result).strip() or "UNKNOWN"] += 1
    summary = {
        "row_count": row_count,
        # ``ELIGIBLE`` is retained as a compatibility input for callers that
        # provide synthetic rows.  Production diagnostics emit the explicit
        # RUNTIME_ELIGIBLE state instead.
        "eligible_count": result_counts.get(RUNTIME_ELIGIBLE, 0) + result_counts.get("ELIGIBLE", 0),
        "runtime_eligible_count": result_counts.get(RUNTIME_ELIGIBLE, 0) + result_counts.get("ELIGIBLE", 0),
        "static_eligible_count": static_result_counts.get("ELIGIBLE", 0),
        "runtime_unknown_count": result_counts.get("RUNTIME_UNKNOWN", 0),
        "runtime_not_probed_count": result_counts.get(RUNTIME_NOT_PROBED, 0),
        "runtime_unavailable_count": result_counts.get(RUNTIME_UNAVAILABLE, 0),
        "result_counts": dict(sorted(result_counts.items())),
        "static_result_counts": dict(sorted(static_result_counts.items())),
        "provider_counts": dict(sorted(provider_counts.items())),
    }
    if coverage is not None:
        summary["catalog_coverage"] = dict(coverage)
    if candidate_promotion is not None:
        summary["candidate_promotion"] = dict(candidate_promotion)
    return summary


def catalog_coverage(evidence: ModelEvidenceCatalog, *, now: datetime | None = None) -> dict[str, Any]:
    """Report current and stale stored discovery coverage without secrets."""

    all_entries = evidence.catalog.all_entries()
    current_identities = {entry.identity for entry in evidence.catalog.entries(now=now)}
    stale_entries = [entry for entry in all_entries if entry.identity not in current_identities]

    def counts(entries: Iterable[Any]) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for entry in entries:
            counter[f"{entry.provider_id}:{entry.provider_binding_id}"] += 1
        return dict(sorted(counter.items()))

    return {
        "stored_catalog_count": len(all_entries),
        "current_catalog_count": len(current_identities),
        "stale_or_expired_count": len(stale_entries),
        "current_provider_binding_counts": counts(entry for entry in all_entries if entry.identity in current_identities),
        "stale_or_expired_provider_binding_counts": counts(stale_entries),
    }


def summarize_candidate_evidence(document: Mapping[str, Any]) -> dict[str, Any]:
    """Project why an external discovery candidate was not promoted."""

    if not isinstance(document, Mapping):
        raise ValueError("candidate evidence must be an object")
    canonical = document.get("canonical_admission_snapshot")
    canonical = canonical if isinstance(canonical, Mapping) else {}
    generation = document.get("generation")
    generation = generation if isinstance(generation, Mapping) else {}
    reasons: list[str] = []
    if canonical.get("candidate_not_merged") is True:
        reasons.append("candidate_not_merged")
    if generation.get("attempted") is False:
        reasons.append("generation_not_attempted")
    if canonical.get("runtime_eligible_count") == 0:
        reasons.append("no_runtime_eligible_route")
    if generation.get("paid_route_added") is False:
        reasons.append("paid_route_not_added")
    return {
        "status": "NOT_PROMOTED" if reasons else "NO_EXPLICIT_BLOCKER",
        "candidate_entry_count": (document.get("host_discovery") or {}).get("candidate_entry_count") if isinstance(document.get("host_discovery"), Mapping) else None,
        "refreshed_binding_count": (document.get("host_discovery") or {}).get("refreshed_binding_count") if isinstance(document.get("host_discovery"), Mapping) else None,
        "reasons": reasons,
    }


def _load_json_document(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"evidence document is unreadable: {path.name}") from exc
    if not isinstance(value, Mapping):
        raise ValueError(f"evidence document must be an object: {path.name}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, default=ROOT / "spec" / "v2" / "model_evidence")
    parser.add_argument("--provider")
    parser.add_argument("--binding")
    parser.add_argument("--model")
    parser.add_argument(
        "--runtime-evidence",
        type=Path,
        help="optional exact-identity runtime admission snapshot; no network or generation probe is performed",
    )
    parser.add_argument(
        "--candidate-evidence",
        type=Path,
        help="optional discovery candidate evidence used to report explicit promotion blockers",
    )
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
        evidence = ModelEvidenceCatalog.load(args.evidence_dir)
        runtime_snapshot = RuntimeAdmissionSnapshot.empty()
        if args.runtime_evidence is not None:
            runtime_snapshot = RuntimeAdmissionSnapshot.from_document(_load_json_document(args.runtime_evidence))
        rows = diagnose_entries(
            evidence,
            runtime_snapshot=runtime_snapshot,
            provider_id=args.provider,
            provider_binding_id=args.binding,
            model_id=args.model,
            limit=limit,
        )
    except Exception as exc:
        print(json.dumps({"status": "failed", "category": type(exc).__name__}, ensure_ascii=False))
        return 2
    if args.summary:
        summary = summarize_entries(
            rows,
            coverage=catalog_coverage(evidence),
            candidate_promotion=(
                summarize_candidate_evidence(_load_json_document(args.candidate_evidence))
                if args.candidate_evidence is not None
                else {"status": "NOT_PROVIDED", "reasons": []}
            ),
        )
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
        headers = ("provider", "binding", "model", "discovery", "benchmark", "capability", "billing", "qualification", "runtime", "gate_reason", "result")
        print("\t".join(headers))
        for row in rows:
            print(
                "\t".join(
                    str(row.get(key, "-"))
                    for key in ("provider_id", "provider_binding_id", "model_id", "discovery", "benchmark", "capability", "billing", "qualification", "runtime", "gate_reason", "result")
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

