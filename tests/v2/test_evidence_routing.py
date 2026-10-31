from datetime import datetime, timezone

import pytest

from scripts.devfarm_metrics import WorkerMetricsError, WorkerMetricsStore
from src.dev_agent.intelligence.evidence_routing import (
    EvidenceRoutingError,
    EvidenceBasedRoutingPolicy,
    EvidenceCandidate,
)


_NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _record(
    store: WorkerMetricsStore,
    *,
    task_id: str,
    request_id: str,
    binding_id: str,
    elapsed_ms: int,
    accepted: bool,
    recorded_at: str,
    provider_id: str = "gemini",
    model_id: str | None = None,
) -> None:
    manifest = {"task_id": task_id, "task_type": "unit"}
    result = {
        "host_verified_tests": [
            {"command": "python -m pytest tests/v2/test_target.py -q", "exit_code": 0, "passed": True}
        ],
        "worker_metrics": {
            "request_id": request_id,
            "provider_id": provider_id,
            "provider_binding_id": binding_id,
            "model_id": model_id or binding_id,
            "intelligence_tier": "L1",
            "elapsed_ms": elapsed_ms,
            "attempt_count": 1,
            "host_verified": True,
            "host_tests_passed": True,
            "result_accepted": accepted,
        },
    }
    store.record(manifest=manifest, result=result, recorded_at=recorded_at)


def test_evidence_policy_requires_samples_before_preference(tmp_path):
    with WorkerMetricsStore(tmp_path / "metrics.sqlite3") as store:
        _record(
            store,
            task_id="one",
            request_id="request-one",
            binding_id="gemini:worker",
            model_id="gemini-model",
            elapsed_ms=10,
            accepted=True,
            recorded_at="2026-09-10T11:59:00+00:00",
        )

        policy = EvidenceBasedRoutingPolicy(store, minimum_samples=2, max_age_seconds=3600)
        ranked = policy.rank(
            "unit",
            [
                EvidenceCandidate("gemini", "gemini:worker", "gemini-model"),
                EvidenceCandidate("cloudflare", "cloudflare:worker", "cf-model"),
            ],
            now=_NOW,
        )
        preferred = policy.preferred_bindings(
            "unit", [EvidenceCandidate("gemini", "gemini:worker", "gemini-model")], now=_NOW
        )

    assert [item.binding_id for item in ranked] == ["gemini:worker", "cloudflare:worker"]
    assert all(item.preferred is False for item in ranked)
    assert ranked[0].reason == "insufficient_samples"
    assert preferred == ()


def test_evidence_policy_prefers_measured_success_without_expanding_hard_filtered_scope(tmp_path):
    with WorkerMetricsStore(tmp_path / "metrics.sqlite3") as store:
        for index in range(3):
            _record(
                store,
                task_id=f"fast-{index}",
                request_id=f"fast-request-{index}",
                binding_id="gemini:worker",
                model_id="gemini-model",
                elapsed_ms=20,
                accepted=True,
                recorded_at="2026-09-10T11:59:00+00:00",
            )
            _record(
                store,
                task_id=f"slow-{index}",
                request_id=f"slow-request-{index}",
                binding_id="cloudflare:worker",
                provider_id="cloudflare",
                model_id="cf-model",
                elapsed_ms=80,
                accepted=True,
                recorded_at="2026-09-10T11:59:00+00:00",
            )

        policy = EvidenceBasedRoutingPolicy(store, minimum_samples=3, max_age_seconds=3600)
        ranked = policy.rank(
            "unit",
            [
                EvidenceCandidate("cloudflare", "cloudflare:worker", "cf-model"),
                EvidenceCandidate("gemini", "gemini:worker", "gemini-model"),
            ],
            now=_NOW,
        )
        preferred = policy.preferred_bindings(
            "unit",
            [
                EvidenceCandidate("cloudflare", "cloudflare:worker", "cf-model"),
                EvidenceCandidate("gemini", "gemini:worker", "gemini-model"),
            ],
            now=_NOW,
        )

    assert [item.binding_id for item in ranked] == ["gemini:worker", "cloudflare:worker"]
    assert preferred == (
        EvidenceCandidate("gemini", "gemini:worker", "gemini-model"),
        EvidenceCandidate("cloudflare", "cloudflare:worker", "cf-model"),
    )
    # A binding that was not admitted by the upstream capability/privacy/
    # budget/quota filter cannot be added by this advisory policy.
    assert "unfiltered:binding" not in preferred


def test_evidence_policy_expires_old_history_and_triggers_rollback_for_low_acceptance(tmp_path):
    with WorkerMetricsStore(tmp_path / "metrics.sqlite3") as store:
        for index in range(3):
            _record(
                store,
                task_id=f"old-{index}",
                request_id=f"old-request-{index}",
                binding_id="old:worker",
                model_id="old-model",
                elapsed_ms=10,
                accepted=True,
                recorded_at="2026-09-01T12:00:00+00:00",
            )
            _record(
                store,
                task_id=f"bad-{index}",
                request_id=f"bad-request-{index}",
                binding_id="bad:worker",
                model_id="bad-model",
                elapsed_ms=10,
                accepted=index == 0,
                recorded_at="2026-09-10T11:59:00+00:00",
            )

        policy = EvidenceBasedRoutingPolicy(
            store,
            minimum_samples=3,
            max_age_seconds=60,
            minimum_acceptance_rate=0.8,
        )
        ranked = policy.rank(
            "unit",
            [
                EvidenceCandidate("gemini", "old:worker", "old-model"),
                EvidenceCandidate("gemini", "bad:worker", "bad-model"),
            ],
            now=_NOW,
        )
        preferred = policy.preferred_bindings(
            "unit",
            [
                EvidenceCandidate("gemini", "old:worker", "old-model"),
                EvidenceCandidate("gemini", "bad:worker", "bad-model"),
            ],
            now=_NOW,
        )

    by_binding = {item.binding_id: item for item in ranked}
    assert by_binding["old:worker"].reason == "evidence_expired"
    assert by_binding["old:worker"].preferred is False
    assert by_binding["bad:worker"].reason == "rollback_threshold"
    assert by_binding["bad:worker"].rollback_triggered is True
    assert preferred == ()


def test_evidence_policy_rejects_malformed_or_unsafe_source_data():
    class BadSource:
        def summarize(self, **_kwargs):
            return [{
                "task_type": "unit",
                "provider_id": "gemini",
                "provider_binding_id": "binding",
                "model_id": "model",
                "sample_count": "three",
            }]

    policy = EvidenceBasedRoutingPolicy(BadSource(), minimum_samples=1)
    with pytest.raises(EvidenceRoutingError, match="sample_count"):
        policy.rank("unit", [EvidenceCandidate("gemini", "binding", "model")], now=_NOW)


def test_evidence_policy_never_transfers_history_between_models_on_one_binding(tmp_path):
    with WorkerMetricsStore(tmp_path / "metrics.sqlite3") as store:
        for index in range(3):
            _record(
                store,
                task_id=f"accepted-{index}",
                request_id=f"accepted-request-{index}",
                binding_id="shared-binding",
                model_id="model-with-accepted-history",
                elapsed_ms=40,
                accepted=True,
                recorded_at="2026-09-10T11:59:00+00:00",
            )
        policy = EvidenceBasedRoutingPolicy(store, minimum_samples=3, max_age_seconds=3600)
        ranked = policy.rank(
            "unit",
            [
                EvidenceCandidate("gemini", "shared-binding", "model-without-history"),
                EvidenceCandidate("gemini", "shared-binding", "model-with-accepted-history"),
            ],
            now=_NOW,
        )

    assert [(item.model_id, item.preferred) for item in ranked] == [
        ("model-with-accepted-history", True),
        ("model-without-history", False),
    ]


def test_worker_metrics_read_only_open_does_not_create_or_mutate_store(tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(WorkerMetricsError, match="does not exist"):
        WorkerMetricsStore.open_read_only(missing)
    assert not missing.exists()

    path = tmp_path / "metrics.sqlite3"
    with WorkerMetricsStore(path) as writable:
        _record(
            writable,
            task_id="readonly",
            request_id="readonly-request",
            binding_id="binding",
            elapsed_ms=10,
            accepted=True,
            recorded_at="2026-09-10T11:59:00+00:00",
        )
    with WorkerMetricsStore.open_read_only(path) as readonly:
        before = readonly.list()
        with pytest.raises(WorkerMetricsError, match="read-only"):
            readonly.record(manifest={}, result={})
        assert readonly.list() == before


def test_worker_metrics_summary_reports_latest_timestamp_and_rejects_bad_age(tmp_path):
    with WorkerMetricsStore(tmp_path / "metrics.sqlite3") as store:
        _record(
            store,
            task_id="summary",
            request_id="summary-request",
            binding_id="binding",
            elapsed_ms=10,
            accepted=True,
            recorded_at="2026-09-10T11:59:00+00:00",
        )
        summary = store.summarize(task_type="unit", minimum_samples=1)
        assert summary[0]["latest_recorded_at"] == "2026-09-10T11:59:00+00:00"
        with pytest.raises(WorkerMetricsError, match="max_age_seconds"):
            store.summarize(max_age_seconds=-1)
