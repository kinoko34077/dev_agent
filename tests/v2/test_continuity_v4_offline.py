from datetime import datetime, timezone
import time

from scripts.devfarm_metrics import WorkerMetricsStore
from src.dev_agent.domain.protocol import TaskStatus
from src.dev_agent.intelligence.evidence_routing import (
    EvidenceBasedRoutingPolicy,
    EvidenceCandidate,
)
from src.dev_agent.operation import OperationConfig, OperationService
from src.dev_agent.operation_runtime import RuntimeCoordinator


_NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


def _config(tmp_path):
    return OperationConfig(
        data_dir=tmp_path / "runtime",
        provider_id="fake",
        model="deterministic",
        worker_id="v4-offline-worker",
        idle_sleep_seconds=0.01,
    )


def _accepted_worker_result(*, request_id: str) -> dict:
    return {
        "host_verified_tests": [
            {"command": "python -m pytest tests/v2/test_target.py -q", "exit_code": 0, "passed": True}
        ],
        "worker_metrics": {
            "request_id": request_id,
            "provider_id": "ollama",
            "provider_binding_id": "ollama:local:gemma4-12b",
            "model_id": "gemma4:12b",
            "intelligence_tier": "L1",
            "elapsed_ms": 10,
            "attempt_count": 1,
            "host_verified": True,
            "host_tests_passed": True,
            "result_accepted": True,
        },
    }


def test_offline_v4_two_units_feed_next_choice_and_resume_after_interrupt(tmp_path):
    """Compose existing boundaries without claiming a live Worker loop."""

    config = _config(tmp_path)
    first = OperationService.submit(config, "complete the first bounded worker unit", task_type="worker")

    with RuntimeCoordinator.open(config, revision="v4-revision", instance_id="v4-runtime-1") as runtime:
        completed_first = runtime.run_once()

    assert completed_first is not None
    assert completed_first.task_id == first.task_id
    assert completed_first.status is TaskStatus.COMPLETED

    metrics_path = tmp_path / "metrics.sqlite3"
    with WorkerMetricsStore(metrics_path) as store:
        store.record(
            manifest={"task_id": first.task_id, "task_type": "worker"},
            result=_accepted_worker_result(request_id="v4-request-1"),
            recorded_at="2026-11-01T11:59:00+00:00",
        )
        candidates = (
            EvidenceCandidate("ollama", "ollama:local:gemma4-12b", "gemma4:12b"),
            EvidenceCandidate("gemini", "gemini:worker", "gemini-model"),
        )
        next_choice = EvidenceBasedRoutingPolicy(
            store,
            minimum_samples=1,
            max_age_seconds=3600,
        ).preferred_bindings("worker", candidates, now=_NOW)

    assert next_choice == (candidates[0],)

    second = OperationService.submit(config, "resume the second bounded worker unit", task_type="worker")
    OperationService.request_user_delay(config, second.task_id, 1, source="v4 bounded interruption")

    with RuntimeCoordinator.open(config, revision="v4-revision", instance_id="v4-runtime-2") as runtime:
        assert runtime.run_once() is None

    time.sleep(1.05)
    with RuntimeCoordinator.open(config, revision="v4-revision", instance_id="v4-runtime-3") as restarted:
        resumed = restarted.run_once()

    assert resumed is not None
    assert resumed.task_id == second.task_id
    assert resumed.status is TaskStatus.COMPLETED
    assert resumed.task_id != first.task_id
    status = OperationService.read_status(config, second.task_id)
    assert status["execution_attempts"] == 1
