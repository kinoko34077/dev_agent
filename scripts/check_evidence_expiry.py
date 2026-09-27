"""Report time-bounded admission evidence before it silently expires.

Read-only and offline: no network, generation, credential, or state mutation.
It projects the canonical model evidence, the trusted billing catalog, and the
Provider qualification matrix at ``now`` and at ``now + horizon`` so an
operator sees an expiry cliff (#27 D1, #26 B) before routing fails closed.

"Static ELIGIBLE" is the offline model-evidence projection only (discovery,
alias, benchmark, capability). It is NOT runtime admission: qualification,
billing, quota, health, and freshness gates are evaluated separately by the
ResourceRouter and are never implied by this report.

Modes:
- default: report only, exit 0.
- ``--fail-if-no-eligible-within HOURS`` (warning mode, used by v2-core CI with
  continue-on-error): exit 1 on an expiry cliff, with a ``::warning::``.
- ``--preflight`` (R9 / promotion preflight, blocking): exit 1 with an
  ``::error::`` when static ELIGIBLE is 0 at the horizon, a source that admits
  routes now is exhausted at the horizon, or any ``--require-identity`` is not
  statically ELIGIBLE both now and at the horizon.
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


# Record-level validity start keys, in priority order.  Model evidence uses
# observed_at, the qualification matrix tested_at, and billing verified_at.
_START_KEYS = ("observed_at", "tested_at", "verified_at")
Window = tuple["datetime | None", datetime]


def _valid_at(window: Window, at: datetime) -> bool:
    start, expires = window
    return (start is None or start <= at) and at < expires


def _window(windows: list[Window], now: datetime, horizon: datetime) -> dict[str, Any]:
    """Count records whose own validity window covers now / the horizon.

    A record is current only when ``start <= t < expires``; a future-dated
    observation is not current even though its expiry is in the future.
    """

    current = [window for window in windows if _valid_at(window, now)]
    return {
        "total": len(windows),
        "current": len(current),
        "current_at_horizon": sum(1 for window in windows if _valid_at(window, horizon)),
        "not_yet_valid": sum(1 for start, _ in windows if start is not None and start > now),
        "next_expiry": min((expires for _, expires in current), default=None),
    }


def _eligible(evidence: ModelEvidenceCatalog, at: datetime) -> int:
    resolver = evidence.resolver
    return sum(
        1
        for entry in evidence.catalog.entries(now=at)
        if resolver.diagnose(entry.provider_id, entry.provider_binding_id, entry.model_id, now=at).result == "ELIGIBLE"
    )


def _record_window(record: Any) -> Window | None:
    if not isinstance(record, dict):
        return None
    expires = _parse(record.get("expires_at"))
    if expires is None:
        return None
    start = next((_parse(record.get(key)) for key in _START_KEYS if record.get(key) is not None), None)
    return (start, expires)


def _file_windows(path: Path) -> list[Window]:
    found: list[Window] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            window = _record_window(node)
            if window is not None:
                found.append(window)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(path.read_text(encoding="utf-8")))
    return found


def _status(evidence: ModelEvidenceCatalog, identity: tuple[str, str, str], at: datetime) -> str:
    return evidence.resolver.diagnose(*identity, now=at).result


def build_report(
    *,
    now: datetime,
    horizon_hours: float,
    evidence_directory: Path = DEFAULT_MODEL_EVIDENCE_DIRECTORY,
    required_identities: tuple[tuple[str, str, str], ...] = (),
) -> dict[str, Any]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    horizon = now + timedelta(hours=horizon_hours)
    evidence = ModelEvidenceCatalog.load(evidence_directory)
    sources: dict[str, Any] = {}
    for path in sorted(Path(evidence_directory).glob("*.json")):
        windows = _file_windows(path)
        if windows:
            sources[f"model_evidence/{path.name}"] = _window(windows, now, horizon)
    sources["billing_catalog"] = _window(
        [
            window
            for window in (
                _record_window({"verified_at": profile.verified_at, "expires_at": profile.expires_at})
                for profile in TRUSTED_RESOURCE_CATALOG.values()
            )
            if window is not None
        ],
        now,
        horizon,
    )
    if QUALIFICATION_MATRIX.exists():
        sources["qualification_matrix"] = _window(_file_windows(QUALIFICATION_MATRIX), now, horizon)
    exhausted = [name for name, window in sources.items() if window["current"] > 0 and window["current_at_horizon"] == 0]
    for window in sources.values():
        if window["next_expiry"] is not None:
            window["next_expiry"] = window["next_expiry"].isoformat()
    identities = [
        {
            "provider_id": identity[0],
            "provider_binding_id": identity[1],
            "model_id": identity[2],
            "static_now": _status(evidence, identity, now),
            "static_at_horizon": _status(evidence, identity, horizon),
        }
        for identity in required_identities
    ]
    eligible_now = _eligible(evidence, now)
    eligible_at_horizon = _eligible(evidence, horizon)
    failures: list[str] = []
    if eligible_at_horizon == 0:
        failures.append("no_static_eligible_at_horizon")
    failures.extend(f"source_exhausted:{name}" for name in exhausted)
    failures.extend(
        "identity_not_eligible:{provider_id}/{provider_binding_id}/{model_id}".format(**row)
        for row in identities
        if row["static_now"] != "ELIGIBLE" or row["static_at_horizon"] != "ELIGIBLE"
    )
    return {
        "scope": "static_model_evidence_only; not runtime admission",
        "now": now.isoformat(),
        "horizon": horizon.isoformat(),
        "static_eligible_now": eligible_now,
        "static_eligible_at_horizon": eligible_at_horizon,
        "sources": sources,
        "sources_exhausted_at_horizon": exhausted,
        "required_identities": identities,
        "failures": failures,
    }


def _identity(value: str) -> tuple[str, str, str]:
    parts = tuple(part.strip() for part in value.split("/", 2)) if value.count("/") >= 2 else ()
    # provider_binding_id may itself contain ':' but not '/', and model ids may contain '/'.
    if len(parts) != 3 or not all(parts):
        raise argparse.ArgumentTypeError("identity must be PROVIDER/BINDING/MODEL")
    return parts  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--horizon-hours", type=float, default=48.0)
    parser.add_argument("--now", help="timezone-aware ISO timestamp to evaluate at (default: current UTC time)")
    parser.add_argument("--evidence-dir", type=Path, default=DEFAULT_MODEL_EVIDENCE_DIRECTORY)
    parser.add_argument("--require-identity", type=_identity, action="append", default=[], metavar="PROVIDER/BINDING/MODEL")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fail-if-no-eligible-within", type=float, metavar="HOURS", help="warning mode")
    mode.add_argument("--preflight", action="store_true", help="blocking R9/promotion preflight")
    args = parser.parse_args(argv)
    if args.now:
        now = _parse(args.now)
        if now is None or not any(marker in args.now[10:] for marker in ("+", "-", "Z", "z")):
            parser.error("--now must be a timezone-aware ISO timestamp")
    else:
        now = datetime.now(timezone.utc)
    horizon_hours = args.fail_if_no_eligible_within if args.fail_if_no_eligible_within is not None else args.horizon_hours
    report = build_report(
        now=now,
        horizon_hours=horizon_hours,
        evidence_directory=args.evidence_dir,
        required_identities=tuple(args.require_identity),
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report["failures"]:
        return 0
    if args.preflight:
        print(f"::error::admission evidence preflight failed ({horizon_hours:g}h): {report['failures']}", file=sys.stderr)
        return 1
    if args.fail_if_no_eligible_within is not None:
        print(f"::warning::admission evidence expires within {horizon_hours:g}h: {report['failures']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
