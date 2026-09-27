from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import shutil

import pytest

from src.dev_agent.resources.billing_catalog import TRUSTED_RESOURCE_CATALOG
from src.dev_agent.resources.model_evidence import DEFAULT_MODEL_EVIDENCE_DIRECTORY

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_evidence_expiry.py"
_spec = importlib.util.spec_from_file_location("check_evidence_expiry", _SCRIPT)
check_evidence_expiry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_evidence_expiry)

_FREE3 = ("gemini", "gemini:worker:free-3", "gemini-3.5-flash-lite")
_FREE3_ARG = "/".join(_FREE3)
# A time at which the recorded evidence admits the free-3 identity statically.
_NOW = datetime(2026, 9, 27, 8, tzinfo=timezone.utc)
_FAR_FUTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)
_MODEL_FILES = ("model_catalog_snapshot.json", "model_benchmark_snapshot.json", "model_capability_snapshot.json")


def _rewrite_expiries(node, value: str) -> None:
    if isinstance(node, dict):
        if "expires_at" in node:
            node["expires_at"] = value
        for child in node.values():
            _rewrite_expiries(child, value)
    elif isinstance(node, list):
        for child in node:
            _rewrite_expiries(child, value)


def _evidence_expiring_at(tmp_path: Path, expires_at: datetime) -> Path:
    """Copy canonical evidence and move every model-evidence expiry to one cliff."""
    directory = tmp_path / "evidence"
    shutil.copytree(DEFAULT_MODEL_EVIDENCE_DIRECTORY, directory)
    for name in _MODEL_FILES:
        path = directory / name
        document = json.loads(path.read_text(encoding="utf-8"))
        _rewrite_expiries(document, expires_at.isoformat())
        path.write_text(json.dumps(document), encoding="utf-8")
    return directory


def _report(directory: Path, *, now: datetime = _NOW, horizon_hours: float = 48, identities=(_FREE3,)):
    return check_evidence_expiry.build_report(
        now=now, horizon_hours=horizon_hours, evidence_directory=directory, required_identities=identities
    )


def test_eligible_now_but_zero_at_horizon_is_a_cliff(tmp_path):
    directory = _evidence_expiring_at(tmp_path, _NOW + timedelta(hours=47))

    report = _report(directory)

    assert report["static_eligible_now"] > 0
    assert report["static_eligible_at_horizon"] == 0
    assert "no_static_eligible_at_horizon" in report["failures"]
    assert set(report["sources_exhausted_at_horizon"]) == {f"model_evidence/{name}" for name in _MODEL_FILES}


def test_horizon_boundary_just_before_and_after_expiry(tmp_path):
    expiry = _NOW + timedelta(hours=48)
    directory = _evidence_expiring_at(tmp_path, expiry)

    before = _report(directory, horizon_hours=48 - 1 / 60)  # horizon one minute before the cliff
    after = _report(directory, horizon_hours=48 + 1 / 60)  # horizon one minute after the cliff
    exactly = _report(directory, horizon_hours=48)  # expiry is exclusive: expired at its timestamp

    assert before["static_eligible_at_horizon"] > 0 and before["failures"] == []
    assert after["static_eligible_at_horizon"] == 0
    assert exactly["static_eligible_at_horizon"] == 0


def test_exhaustion_is_reported_only_for_sources_that_admit_routes_now(tmp_path):
    directory = _evidence_expiring_at(tmp_path, _NOW + timedelta(hours=47))

    report = _report(directory)

    assert not any(name in {"billing_catalog", "qualification_matrix"} for name in report["sources_exhausted_at_horizon"])
    expired_already = _report(directory, now=_FAR_FUTURE)
    assert expired_already["sources_exhausted_at_horizon"] == []  # nothing current, so nothing to exhaust


def test_free3_identity_regression(tmp_path):
    healthy = _report(_evidence_expiring_at(tmp_path / "a", _NOW + timedelta(hours=72)))
    cliff = _report(_evidence_expiring_at(tmp_path / "b", _NOW + timedelta(hours=47)))

    assert healthy["required_identities"][0]["static_now"] == "ELIGIBLE"
    assert healthy["required_identities"][0]["static_at_horizon"] == "ELIGIBLE"
    assert healthy["failures"] == []
    assert cliff["required_identities"][0]["static_now"] == "ELIGIBLE"
    assert cliff["required_identities"][0]["static_at_horizon"] != "ELIGIBLE"
    assert f"identity_not_eligible:{_FREE3_ARG}" in cliff["failures"]


def test_report_is_labelled_static_not_runtime(tmp_path):
    report = _report(_evidence_expiring_at(tmp_path, _NOW + timedelta(hours=72)))

    assert report["scope"] == "static_model_evidence_only; not runtime admission"
    assert not any("RUNTIME_ELIGIBLE" in key for key in report)
    assert "NOT runtime admission" in check_evidence_expiry.__doc__


def test_billing_catalog_next_expiry_matches_trusted_profiles():
    earliest = min(datetime.fromisoformat(profile.expires_at) for profile in TRUSTED_RESOURCE_CATALOG.values())
    report = check_evidence_expiry.build_report(now=earliest - timedelta(days=365), horizon_hours=1)

    assert report["sources"]["billing_catalog"]["next_expiry"] == earliest.isoformat()


def test_parse_handles_naive_and_malformed_timestamps():
    assert check_evidence_expiry._parse("2026-09-28T15:00:00") == datetime(2026, 9, 28, 15, tzinfo=timezone.utc)
    assert check_evidence_expiry._parse("not-a-date") is None
    assert check_evidence_expiry._parse("") is None
    assert check_evidence_expiry._parse(None) is None
    with pytest.raises(ValueError):
        check_evidence_expiry.build_report(now=datetime(2026, 9, 27), horizon_hours=1)


@pytest.mark.parametrize("value", ["not-a-date", "2026-09-27T08:00:00"])
def test_cli_rejects_malformed_or_naive_now(value):
    with pytest.raises(SystemExit) as excinfo:
        check_evidence_expiry.main(["--now", value])
    assert excinfo.value.code == 2


def test_cli_modes(tmp_path, capsys):
    cliff = str(_evidence_expiring_at(tmp_path / "cliff", _NOW + timedelta(hours=47)))
    healthy = str(_evidence_expiring_at(tmp_path / "ok", _NOW + timedelta(hours=72)))
    base = ["--now", _NOW.isoformat(), "--require-identity", _FREE3_ARG]

    assert check_evidence_expiry.main([*base, "--evidence-dir", cliff]) == 0  # report-only
    assert check_evidence_expiry.main([*base, "--evidence-dir", cliff, "--fail-if-no-eligible-within", "48"]) == 1
    assert "::warning::" in capsys.readouterr().err
    assert check_evidence_expiry.main([*base, "--evidence-dir", cliff, "--preflight"]) == 1
    assert "::error::" in capsys.readouterr().err
    assert check_evidence_expiry.main([*base, "--evidence-dir", healthy, "--preflight"]) == 0


def test_cli_rejects_bad_identity():
    with pytest.raises(SystemExit) as excinfo:
        check_evidence_expiry.main(["--require-identity", "gemini-only"])
    assert excinfo.value.code == 2
