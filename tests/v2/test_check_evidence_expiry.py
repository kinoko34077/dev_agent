from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
from pathlib import Path

from src.dev_agent.resources.billing_catalog import TRUSTED_RESOURCE_CATALOG

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_evidence_expiry.py"
_spec = importlib.util.spec_from_file_location("check_evidence_expiry", _SCRIPT)
check_evidence_expiry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_evidence_expiry)

# Far past every recorded expiry, independent of the evidence snapshot's dates.
_FAR_FUTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)


def test_report_shows_expiry_cliff_after_all_evidence_expires():
    report = check_evidence_expiry.build_report(now=_FAR_FUTURE, horizon_hours=48)

    assert report["static_eligible_now"] == 0
    assert report["static_eligible_at_horizon"] == 0
    for window in report["sources"].values():
        assert window["current"] == 0 and window["next_expiry"] is None


def test_billing_catalog_next_expiry_matches_trusted_profiles():
    earliest = min(datetime.fromisoformat(profile.expires_at) for profile in TRUSTED_RESOURCE_CATALOG.values())
    before = earliest.replace(year=earliest.year - 1)

    report = check_evidence_expiry.build_report(now=before, horizon_hours=1)

    assert report["sources"]["billing_catalog"]["next_expiry"] == earliest.isoformat()


def test_fail_flag_exits_nonzero_only_when_requested(capsys):
    assert check_evidence_expiry.main(["--now", _FAR_FUTURE.isoformat(), "--fail-if-no-eligible-within", "48"]) == 1
    assert "static_eligible_at_horizon=0" in capsys.readouterr().err
    assert check_evidence_expiry.main(["--now", _FAR_FUTURE.isoformat()]) == 0
