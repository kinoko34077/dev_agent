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
_FAR_FUTURE = datetime(2100, 1, 1, tzinfo=timezone.utc)
_MODEL_FILES = ("model_catalog_snapshot.json", "model_benchmark_snapshot.json", "model_capability_snapshot.json")


def _latest_observation_time() -> datetime:
    timestamps = []
    for name in _MODEL_FILES:
        document = json.loads((DEFAULT_MODEL_EVIDENCE_DIRECTORY / name).read_text(encoding="utf-8"))

        def collect(node) -> None:
            if isinstance(node, dict):
                value = node.get("observed_at")
                if isinstance(value, str):
                    timestamps.append(datetime.fromisoformat(value).astimezone(timezone.utc))
                for child in node.values():
                    collect(child)
            elif isinstance(node, list):
                for child in node:
                    collect(child)

        collect(document)
    return max(timestamps) + timedelta(minutes=1)


# Keep the fixture evaluation after the newest canonical observation. Evidence
# refreshes legitimately move observed_at forward; the test must not encode a
# stale wall-clock assumption that predates the refreshed snapshot.
_NOW = _latest_observation_time()


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
    latest_start = max(datetime.fromisoformat(profile.verified_at) for profile in TRUSTED_RESOURCE_CATALOG.values())
    assert latest_start < earliest  # every profile's window overlaps [latest_start, earliest)

    inside = check_evidence_expiry.build_report(now=latest_start, horizon_hours=1)
    before = check_evidence_expiry.build_report(now=latest_start - timedelta(days=365), horizon_hours=1)

    assert inside["sources"]["billing_catalog"]["next_expiry"] == earliest.isoformat()
    assert inside["sources"]["billing_catalog"]["current"] == len(TRUSTED_RESOURCE_CATALOG)
    assert before["sources"]["billing_catalog"]["current"] == 0  # not yet verified


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


def _rewrite_key(node, key: str, value: str) -> None:
    if isinstance(node, dict):
        if key in node:
            node[key] = value
        for child in node.values():
            _rewrite_key(child, key, value)
    elif isinstance(node, list):
        for child in node:
            _rewrite_key(child, key, value)


def test_mixed_window_source_counts_only_records_valid_at_each_time(tmp_path):
    iso = lambda hours: (_NOW + timedelta(hours=hours)).isoformat()  # noqa: E731
    path = tmp_path / "mixed.json"
    path.write_text(
        json.dumps(
            {
                "entries": [
                    {"observed_at": iso(-100), "expires_at": iso(-1)},  # expired before now
                    {"observed_at": iso(-10), "expires_at": iso(10)},  # current now, gone at +48h
                    {"observed_at": iso(-10), "expires_at": iso(100)},  # current now and at +48h
                    {"observed_at": iso(1), "expires_at": iso(100)},  # future-observed: not current now
                    {"observed_at": iso(60), "expires_at": iso(100)},  # not yet valid even at +48h
                ]
            }
        ),
        encoding="utf-8",
    )

    window = check_evidence_expiry._window(
        check_evidence_expiry._file_windows(path), _NOW, _NOW + timedelta(hours=48)
    )

    assert window["total"] == 5
    assert window["current"] == 2
    assert window["current_at_horizon"] == 2  # the long-lived record and the one observed at +1h
    assert window["not_yet_valid"] == 2
    assert window["next_expiry"] == _NOW + timedelta(hours=10)  # only among records current now


def test_future_observed_evidence_is_not_current_nor_exhausted(tmp_path):
    directory = _evidence_expiring_at(tmp_path, _NOW + timedelta(hours=100))
    for name in _MODEL_FILES:
        path = directory / name
        document = json.loads(path.read_text(encoding="utf-8"))
        _rewrite_key(document, "observed_at", (_NOW + timedelta(hours=1)).isoformat())
        path.write_text(json.dumps(document), encoding="utf-8")

    report = _report(directory)

    for name in _MODEL_FILES:
        source = report["sources"][f"model_evidence/{name}"]
        assert source["current"] == 0
        assert source["not_yet_valid"] == source["total"]
        assert source["next_expiry"] is None
    assert not any(name.startswith("model_evidence/") for name in report["sources_exhausted_at_horizon"])
    assert report["static_eligible_now"] == 0  # consistent with the resolver's own window check
