import json

import pytest

from scripts.devfarm_metrics import WorkerMetricsError, WorkerMetricsStore
from scripts.devfarm_worker import apply_and_verify, run_worker
from tests.v2.devfarm_test_support import _WorkerProvider, _workspace, _patch


def _proposal_output():
    return {
        "status": "completed",
        "changed_files": ["tests/v2/test_target.py"],
        "tests_run": [],
        "tests_passed": True,
        "known_issues": [],
        "assumptions": [],
        "patch": _patch(),
        "notes": "proposal ready",
    }


def test_host_verification_accumulates_safe_metrics_and_exposes_summary(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    run_worker(root, manifest_path, provider=_WorkerProvider(_proposal_output()))

    verified = apply_and_verify(root, manifest_path)

    assert verified["worker_metrics"]["durable_recorded"] is True
    with WorkerMetricsStore(root / ".devfarm" / "metrics.sqlite3") as store:
        records = store.list()
        assert len(records) == 1
        record = records[0]
        assert record["task_id"] == "worker-test-001"
        assert record["provider_id"] == "cloudflare"
        assert record["model_id"] == "test-model"
        assert record["task_type"] == "unspecified"
        assert record["host_verified"] is True
        assert record["host_tests_passed"] is True
        assert record["result_accepted"] is True
        assert record["host_tests"][0]["passed"] is True
        summary = store.summarize(task_type="unspecified", minimum_samples=1)
        assert summary[0]["sample_count"] == 1
        assert summary[0]["acceptance_rate"] == 1.0


def test_worker_metrics_upsert_is_idempotent_and_does_not_use_model_claims(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    proposed = run_worker(root, manifest_path, provider=_WorkerProvider(_proposal_output()))
    verified = dict(proposed)
    verified["host_verified_tests"] = [{"command": "python -m pytest tests/v2/test_target.py -q", "exit_code": 0, "passed": True}]
    verified["worker_metrics"] = {
        **proposed["worker_metrics"],
        "host_verified": True,
        "host_tests_passed": True,
        "result_accepted": True,
    }
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    with WorkerMetricsStore(root / ".devfarm" / "metrics.sqlite3") as store:
        first = store.record(manifest=manifest, result=verified, recorded_at="2026-09-10T00:00:00+00:00")
        second = store.record(manifest=manifest, result=verified, recorded_at="2026-09-10T00:01:00+00:00")
        assert first["metric_id"] == second["metric_id"]
        assert len(store.list()) == 1
        assert store.list()[0]["recorded_at"] == "2026-09-10T00:01:00+00:00"

        unverified = dict(verified)
        unverified["worker_metrics"] = {**verified["worker_metrics"], "host_verified": False}
        with pytest.raises(WorkerMetricsError, match="host-verified"):
            store.record(manifest=manifest, result=unverified)
