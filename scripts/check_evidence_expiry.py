"""Report time-bounded admission evidence before it silently expires.

Read-only and offline: no network, generation, credential, or state mutation.
It projects the canonical model evidence, the trusted billing catalog, and the
Provider qualification matrix at ``now`` and at ``now + horizon`` so an
operator sees an expiry cliff (#27 D1, #26 B) before routing fails closed.

Exit status: 0 normally.  With ``--fail-if-no-eligible-within HOURS`` it exits
1 when no model identity would remain statically ELIGIBLE at that horizon, or
when a source that currently admits routes would have none left.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.dev_agent.resources.billing_catalog import TRUSTED_RESOURCE_CATALOG  # noqa: E402
from src.dev_agent.resources.model_evidence import DEFAULT_MODEL_EVIDENCE_DIRECTORY, ModelEvidenceCatalog  # noqa: E402

QUALIFICATION_MATRIX = ROOT / "spec" / "v2" / "PROVIDER_CAPABILITY_MATRIX.json"


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def _window(expiries: list[datetime], now: datetime, horizon: datetime) -> dict[str, Any]:
    return {
        "total": len(expiries),
        "current": sum(1 for value in expiries if value > now),
        "current_at_horizon": sum(1 for value in expiries if value > horizon),
        "next_expiry": min((value for value in expiries if value > now), default=None),
    }


def _eligible(evidence: ModelEvidenceCatalog, at: datetime) -> int:
    resolver = evidence.resolver
    return sum(
        1
        for entry in evidence.catalog.entries(now=at)
        if resolver.diagnose(entry.provider_id, entry.provider_binding_id, entry.model_id, now=at).result == "ELIGIBLE"
    )


def _file_expiries(path: Path) -> list[datetime]:
    found: list[datetime] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            parsed = _parse(node.get("expires_at"))
            if parsed is not None:
                found.append(parsed)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(path.read_text(encoding="utf-8")))
    return found


def build_report(*, now: datetime, horizon_hours: float, evidence_directory: Path = DEFAULT_MODEL_EVIDENCE_DIRECTORY) -> dict[str, Any]:
    horizon = now + timedelta(hours=horizon_hours)
    evidence = ModelEvidenceCatalog.load(evidence_directory)
    sources: dict[str, Any] = {}
    for path in sorted(Path(evidence_directory).glob("*.json")):
        expiries = _file_expiries(path)
        if expiries:
            sources[f"model_evidence/{path.name}"] = _window(expiries, now, horizon)
    sources["billing_catalog"] = _window(
        [value for value in (_parse(profile.expires_at) for profile in TRUSTED_RESOURCE_CATALOG.values()) if value is not None],
        now,
        horizon,
    )
    if QUALIFICATION_MATRIX.exists():
        sources["qualification_matrix"] = _window(_file_expiries(QUALIFICATION_MATRIX), now, horizon)
    for window in sources.values():
        if window["next_expiry"] is not None:
            window["next_expiry"] = window["next_expiry"].isoformat()
    return {
        "now": now.isoformat(),
        "horizon": horizon.isoformat(),
        "static_eligible_now": _eligible(evidence, now),
        "static_eligible_at_horizon": _eligible(evidence, horizon),
        "sources": sources,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--horizon-hours", type=float, default=48.0)
    parser.add_argument("--now", help="ISO timestamp to evaluate at (default: current UTC time)")
    parser.add_argument("--fail-if-no-eligible-within", type=float, metavar="HOURS")
    args = parser.parse_args(argv)
    now = _parse(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        parser.error("--now must be an ISO timestamp")
    horizon_hours = args.fail_if_no_eligible_within if args.fail_if_no_eligible_within is not None else args.horizon_hours
    report = build_report(now=now, horizon_hours=horizon_hours)
    exhausted = [
        name
        for name, window in report["sources"].items()
        if window["current"] > 0 and window["current_at_horizon"] == 0
    ]
    report["sources_exhausted_at_horizon"] = exhausted
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.fail_if_no_eligible_within is not None and (report["static_eligible_at_horizon"] == 0 or exhausted):
        print(
            f"::warning::admission evidence expires within {horizon_hours:g}h: "
            f"static_eligible_at_horizon={report['static_eligible_at_horizon']} exhausted={exhausted}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
