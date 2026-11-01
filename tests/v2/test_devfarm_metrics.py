import json
import sqlite3
import threading

import pytest

from scripts.devfarm_metrics import WorkerMetricsError, WorkerMetricsStore
from scripts.devfarm_worker import apply_and_verify, run_worker
from src.dev_agent.domain.protocol import ModelResponse
from tests.v2.devfarm_test_support import _WorkerProvider, _workspace, _patch


def _trusted_apply(root, manifest_path):
    return apply_and_verify(root, manifest_path, trust_level="TRUSTED_HOST_EXEC", operator_approved=True)


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

    verified = _trusted_apply(root, manifest_path)

    assert verified["worker_metrics"]["durable_recorded"] is True
    with WorkerMetricsStore(root / ".devfarm" / "metrics.sqlite3") as store:
        records = store.list()
        assert len(records) == 1
        record = records[0]
        assert record["task_id"] == "worker-test-001"
        assert record["provider_id"] == "cloudflare"
        assert record["model_id"] == "@cf/zai-org/glm-4.7-flash"
        assert record["task_type"] == "unspecified"
        assert record["host_verified"] is True
        assert record["host_tests_passed"] is True
        assert record["result_accepted"] is True
        assert record["host_tests"][0]["passed"] is True
        summary = store.summarize(task_type="unspecified", minimum_samples=1)
        assert summary[0]["sample_count"] == 1
        assert summary[0]["acceptance_rate"] == 1.0


def test_host_verification_metrics_bind_to_written_verification_record(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    run_worker(root, manifest_path, provider=_WorkerProvider(_proposal_output()))

    verified = _trusted_apply(root, manifest_path)

    assert verified["verification_id"]
    with WorkerMetricsStore.open_read_only(root / ".devfarm" / "metrics.sqlite3") as store:
        record = store.list()[0]
    assert record["attempt_id"] == verified["attempt_id"]
    assert record["verification_id"] == verified["verification_id"]


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
        # An exact replay is idempotent: it must not refresh the observation
        # timestamp and thereby extend freshness/expiry by replay alone.
        assert first["recorded_at"] == "2026-09-10T00:00:00+00:00"
        assert second["recorded_at"] == first["recorded_at"]
        assert store.list()[0]["recorded_at"] == "2026-09-10T00:00:00+00:00"

        unverified = dict(verified)
        unverified["worker_metrics"] = {**verified["worker_metrics"], "host_verified": False}
        with pytest.raises(WorkerMetricsError, match="host-verified"):
            store.record(manifest=manifest, result=unverified)


def test_worker_metrics_rejects_conflicting_replay_for_same_task_and_request(tmp_path):
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
        store.record(manifest=manifest, result=verified, recorded_at="2026-09-10T00:00:00+00:00")
        conflicting = json.loads(json.dumps(verified))
        conflicting["worker_metrics"]["result_accepted"] = False
        with pytest.raises(WorkerMetricsError, match="conflicting metric replay"):
            store.record(manifest=manifest, result=conflicting, recorded_at="2026-09-10T00:01:00+00:00")
        assert store.list()[0]["result_accepted"] is True


def test_worker_metrics_rejects_concurrent_conflicting_replay_across_connections(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    proposed = run_worker(root, manifest_path, provider=_WorkerProvider(_proposal_output()))
    verified = dict(proposed)
    verified["host_verified_tests"] = [
        {"command": "python -m pytest tests/v2/test_target.py -q", "exit_code": 0, "passed": True}
    ]
    verified["worker_metrics"] = {
        **proposed["worker_metrics"],
        "host_verified": True,
        "host_tests_passed": True,
        "result_accepted": True,
    }
    conflicting = json.loads(json.dumps(verified))
    conflicting["worker_metrics"]["result_accepted"] = False
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = root / ".devfarm" / "metrics.sqlite3"
    first_store = WorkerMetricsStore(path)
    second_store = WorkerMetricsStore(path)
    start = threading.Barrier(3)
    outside_transaction = []
    outside_transaction_lock = threading.Lock()

    class _RaceConnection:
        def __init__(self, connection):
            self._connection = connection

        @property
        def in_transaction(self):
            return self._connection.in_transaction

        def execute(self, sql, parameters=()):
            normalized = " ".join(sql.split()).upper()
            if normalized.startswith("SELECT ATTEMPT_ID,") and not self._connection.in_transaction:
                with outside_transaction_lock:
                    outside_transaction.append(threading.get_ident())
            return self._connection.execute(sql, parameters)

        def commit(self):
            return self._connection.commit()

        def rollback(self):
            return self._connection.rollback()

        def close(self):
            return self._connection.close()

    first_store.connection = _RaceConnection(first_store.connection)
    second_store.connection = _RaceConnection(second_store.connection)
    outcomes = []
    outcomes_lock = threading.Lock()

    def record(store, result):
        start.wait(timeout=10)
        try:
            store.record(manifest=manifest, result=result, recorded_at="2026-09-10T00:00:00+00:00")
        except Exception as exc:  # pragma: no cover - asserted below
            outcome = ("error", type(exc), str(exc))
        else:
            outcome = ("ok", result["worker_metrics"]["result_accepted"])
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [
        threading.Thread(target=record, args=(first_store, verified)),
        threading.Thread(target=record, args=(second_store, conflicting)),
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=10)
    for thread in threads:
        thread.join(timeout=10)
    first_store.close()
    second_store.close()

    assert all(not thread.is_alive() for thread in threads)
    assert outside_transaction == []
    assert sorted(outcome[0] for outcome in outcomes) == ["error", "ok"]
    winning_value = next(outcome[1] for outcome in outcomes if outcome[0] == "ok")
    with WorkerMetricsStore.open_read_only(path) as store:
        assert store.list()[0]["result_accepted"] is winning_value


def test_worker_metrics_persist_execution_correlation_fields(tmp_path):
    root, manifest_path = _workspace(tmp_path)
    proposed = run_worker(root, manifest_path, provider=_WorkerProvider(_proposal_output()))
    verified = dict(proposed)
    verified["attempt_id"] = "attempt-correlation-001"
    verified["verification_id"] = "verification-correlation-001"
    verified["host_verified_tests"] = [{"command": "python -m pytest tests/v2/test_target.py -q", "exit_code": 0, "passed": True}]
    verified["worker_metrics"] = {
        **proposed["worker_metrics"],
        "host_verified": True,
        "host_tests_passed": True,
        "result_accepted": True,
    }
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    with WorkerMetricsStore(root / ".devfarm" / "metrics.sqlite3") as store:
        store.record(manifest=manifest, result=verified)
        record = store.list()[0]

    assert record["attempt_id"] == "attempt-correlation-001"
    assert record["verification_id"] == "verification-correlation-001"
    assert record["source_revision"] == manifest["base_revision"]


def test_worker_metrics_migrates_v1_schema_without_rewriting_legacy_observation(tmp_path):
    path = tmp_path / "metrics.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE worker_metrics_schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT INTO worker_metrics_schema_meta(key, value) VALUES ('schema_version', '1');
        CREATE TABLE worker_metrics (
            metric_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            provider_binding_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            intelligence_tier TEXT,
            task_type TEXT NOT NULL,
            elapsed_ms INTEGER NOT NULL,
            attempt_count INTEGER NOT NULL,
            retry_count INTEGER NOT NULL,
            usage_json TEXT NOT NULL,
            quota_observation_json TEXT NOT NULL,
            host_tests_json TEXT NOT NULL,
            host_verified INTEGER NOT NULL,
            host_tests_passed INTEGER NOT NULL,
            result_accepted INTEGER NOT NULL,
            codex_correction_chars INTEGER,
            recorded_at TEXT NOT NULL,
            UNIQUE(task_id, request_id)
        );
        INSERT INTO worker_metrics VALUES (
            'metric-v1', 'task-v1', 'request-v1', 'ollama', 'ollama:local',
            'gemma4:12b', NULL, 'worker', 12, 1, 0, '{}', '{}', '[]',
            1, 1, 1, NULL, '2026-11-01T00:00:00+00:00'
        );
        """
    )
    connection.commit()
    connection.close()

    with WorkerMetricsStore(path) as store:
        assert store.SCHEMA_VERSION == 2
        assert store.list()[0]["metric_id"] == "metric-v1"
        assert store.list()[0]["attempt_id"] is None
        assert store.list()[0]["verification_id"] is None
        assert store.list()[0]["source_revision"] is None
        version = store.connection.execute(
            "SELECT value FROM worker_metrics_schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        assert version == "2"

    with WorkerMetricsStore.open_read_only(path) as store:
        assert store.list()[0]["metric_id"] == "metric-v1"


def test_worker_metrics_migration_failure_rolls_back_to_readable_v1(tmp_path, monkeypatch):
    path = tmp_path / "migration-crash.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE worker_metrics_schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT INTO worker_metrics_schema_meta(key, value) VALUES ('schema_version', '1');
        CREATE TABLE worker_metrics (
            metric_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            provider_binding_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            intelligence_tier TEXT,
            task_type TEXT NOT NULL,
            elapsed_ms INTEGER NOT NULL,
            attempt_count INTEGER NOT NULL,
            retry_count INTEGER NOT NULL,
            usage_json TEXT NOT NULL,
            quota_observation_json TEXT NOT NULL,
            host_tests_json TEXT NOT NULL,
            host_verified INTEGER NOT NULL,
            host_tests_passed INTEGER NOT NULL,
            result_accepted INTEGER NOT NULL,
            codex_correction_chars INTEGER,
            recorded_at TEXT NOT NULL,
            UNIQUE(task_id, request_id)
        );
        """
    )
    connection.commit()
    connection.close()

    def crash_after_first_column(cls, database):
        database.execute("ALTER TABLE worker_metrics ADD COLUMN attempt_id TEXT")
        raise RuntimeError("injected migration crash")

    monkeypatch.setattr(
        WorkerMetricsStore,
        "_migrate_1_to_2",
        classmethod(crash_after_first_column),
    )
    with pytest.raises(RuntimeError, match="injected migration crash"):
        WorkerMetricsStore(path)

    with sqlite3.connect(path) as recovered:
        version = recovered.execute(
            "SELECT value FROM worker_metrics_schema_meta WHERE key='schema_version'"
        ).fetchone()[0]
        columns = {
            row[1]
            for row in recovered.execute("PRAGMA table_info(worker_metrics)").fetchall()
        }
    assert version == "1"
    assert "attempt_id" not in columns
    with WorkerMetricsStore.open_read_only(path) as store:
        assert store.list() == []


def test_worker_metrics_open_read_only_accepts_legacy_v1_schema(tmp_path):
    path = tmp_path / "legacy-metrics.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE worker_metrics_schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        INSERT INTO worker_metrics_schema_meta(key, value) VALUES ('schema_version', '1');
        CREATE TABLE worker_metrics (
            metric_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            request_id TEXT NOT NULL,
            provider_id TEXT NOT NULL,
            provider_binding_id TEXT NOT NULL,
            model_id TEXT NOT NULL,
            intelligence_tier TEXT,
            task_type TEXT NOT NULL,
            elapsed_ms INTEGER NOT NULL,
            attempt_count INTEGER NOT NULL,
            retry_count INTEGER NOT NULL,
            usage_json TEXT NOT NULL,
            quota_observation_json TEXT NOT NULL,
            host_tests_json TEXT NOT NULL,
            host_verified INTEGER NOT NULL,
            host_tests_passed INTEGER NOT NULL,
            result_accepted INTEGER NOT NULL,
            codex_correction_chars INTEGER,
            recorded_at TEXT NOT NULL,
            UNIQUE(task_id, request_id)
        );
        """
    )
    connection.commit()
    connection.close()

    with WorkerMetricsStore.open_read_only(path) as store:
        assert store.list() == []
        assert store.summarize(task_type="worker") == []


def test_worker_metrics_summarize_usage_preserves_observation_gaps(tmp_path):
    def verified(task_id, request_id, usage):
        return (
            {"task_id": task_id, "task_type": "production"},
            {
                "worker_metrics": {
                    "request_id": request_id,
                    "provider_id": "gemini",
                    "provider_binding_id": "gemini:worker",
                    "model_id": "gemini-3.5-flash-lite",
                    "intelligence_tier": "L1",
                    "elapsed_ms": 10,
                    "attempt_count": 1,
                    "usage": usage,
                    "host_verified": True,
                    "host_tests_passed": True,
                    "result_accepted": True,
                },
                "host_verified_tests": [],
            },
        )

    with WorkerMetricsStore(tmp_path / "metrics.sqlite3") as store:
        manifest, result = verified(
            "usage-task-1",
            "usage-request-1",
            {"input_tokens": 10, "output_tokens": 4, "refinement_round": 0, "reasoning_effort": "minimal"},
        )
        store.record(manifest=manifest, result=result)
        manifest, result = verified(
            "usage-task-2",
            "usage-request-2",
            {"input_tokens": 5, "output_tokens": 7, "refinement_round": 1, "reasoning_effort": "low"},
        )
        store.record(manifest=manifest, result=result)
        manifest, result = verified(
            "usage-task-3",
            "usage-request-3",
            {"cost_minor": 0},
        )
        store.record(manifest=manifest, result=result)

        summary = store.summarize_usage()

    assert summary["record_count"] == 3
    assert summary["verified_model_calls"] == 3
    assert summary["input_tokens"] is None
    assert summary["output_tokens"] is None
    assert summary["input_token_observation_count"] == 2
    assert summary["output_token_observation_count"] == 2
    assert summary["token_counts_complete"] is False
    assert summary["refinement_rounds"] is None
    assert summary["refinement_round_observation_count"] == 2
    assert summary["model_identities"] == [
        {
            "provider_id": "gemini",
            "provider_binding_id": "gemini:worker",
            "model_id": "gemini-3.5-flash-lite",
            "intelligence_tier": "L1",
        }
    ]
    assert summary["reasoning_efforts"] == ["low", "minimal"]


def test_worker_metrics_summarize_handles_nullable_tiers(tmp_path):
    def verified(task_id, request_id, tier, recorded_at):
        return (
            {"task_id": task_id, "task_type": "worker"},
            {
                "worker_metrics": {
                    "request_id": request_id,
                    "provider_id": "ollama",
                    "provider_binding_id": "ollama:local:gemma4-12b",
                    "model_id": "gemma4:12b",
                    "intelligence_tier": tier,
                    "elapsed_ms": 10,
                    "attempt_count": 1,
                    "host_verified": True,
                    "host_tests_passed": True,
                    "result_accepted": True,
                },
                "host_verified_tests": [],
            },
            recorded_at,
        )

    with WorkerMetricsStore(tmp_path / "metrics.sqlite3") as store:
        for values in (
            verified("worker-tier-none", "request-tier-none", None, "2026-11-01T00:00:00+00:00"),
            verified("worker-tier-l1", "request-tier-l1", "L1", "2026-11-01T00:01:00+00:00"),
        ):
            manifest, result, recorded_at = values
            store.record(manifest=manifest, result=result, recorded_at=recorded_at)

        summary = store.summarize(task_type="worker", minimum_samples=1)

    assert [entry["intelligence_tier"] for entry in summary] == [None, "L1"]
    assert all(entry["sample_count"] == 1 for entry in summary)


def test_worker_metrics_preserve_bounded_refinement_usage_from_provider_response(tmp_path):
    root, manifest_path = _workspace(tmp_path)

    class UsageProvider(_WorkerProvider):
        def request(self, request):
            self.request_count += 1
            output = _proposal_output()
            return ModelResponse(
                provider=self.provider_id,
                model=self.model_id,
                text_segments=[json.dumps(output)],
                usage={
                    "input_tokens": 21,
                    "output_tokens": 8,
                    "refinement_round": 1,
                    "reasoning_effort": "low",
                    "unrelated_provider_payload": "must be omitted",
                },
            )

    result = run_worker(root, manifest_path, provider=UsageProvider({}))

    assert result["worker_metrics"]["usage"] == {
        "input_tokens": 21,
        "output_tokens": 8,
        "refinement_round": 1,
        "reasoning_effort": "low",
    }
