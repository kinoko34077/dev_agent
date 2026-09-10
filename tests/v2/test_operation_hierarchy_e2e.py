from __future__ import annotations

from src.dev_agent.domain.protocol import IntelligenceTier, ModelRequest, TaskStatus, TaskType
from src.dev_agent.intelligence.escalation import EscalationContext
from src.dev_agent.intelligence.evaluator import EvaluationEvidence
from src.dev_agent.operation import OperationConfig, OperationService


def _config(tmp_path):
    return OperationConfig(
        data_dir=tmp_path,
        provider_id="fake",
        model="deterministic",
        worker_id="operation-hierarchy-test",
        idle_sleep_seconds=0.01,
    )


def _retry_evidence(task_id: str, *, attempt: int = 1) -> EvaluationEvidence:
    return EvaluationEvidence(
        task_id=task_id,
        attempt=attempt,
        max_attempts=3,
        objective_met=False,
        deterministic_checks_passed=False,
        policy_compliant=True,
        external_outcome_known=True,
        retryable_failure=True,
        alternate_provider_available=False,
        human_approval_required=False,
    )


def _retry_context(task_id: str, *, attempt: int = 1) -> EscalationContext:
    return EscalationContext(
        task_id=task_id,
        current_tier=IntelligenceTier.L1,
        allowed_tiers=(IntelligenceTier.L1,),
        attempt=attempt,
        max_attempts=3,
        escalation_count=0,
        max_escalations=0,
        now_epoch=100.0,
        deadline_epoch=200.0,
        budget_remaining=0.0,
        estimated_cost=0.0,
        retryable_failure=True,
        same_provider_available=True,
        alternate_provider_available=False,
    )


def test_operation_composes_finite_lifecycle_to_explicit_review(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(
        config,
        "retry a bounded worker task",
        task_type=TaskType.WORKER,
    )

    with OperationService.open(config) as service:
        step = service.evaluate_task(
            _retry_evidence(task.task_id),
            escalation_context=_retry_context(task.task_id),
        )

        assert step.phase == "evaluation"
        assert step.dispatch_cycle.status.value == "awaiting_review"
        assert step.transition.task.status is TaskStatus.READY

        review = service.review_task(
            step,
            actor="operator",
            approved=True,
            approval_reference="operation-hierarchy-review",
        )

    assert review.event_type == "escalation.accepted"


def test_operation_reviewed_lifecycle_dispatch_is_lease_fenced(tmp_path):
    config = _config(tmp_path)
    task = OperationService.submit(
        config,
        "dispatch a reviewed worker retry",
        task_type=TaskType.WORKER,
    )

    with OperationService.open(config) as service:
        step = service.evaluate_task(
            _retry_evidence(task.task_id),
            escalation_context=_retry_context(task.task_id),
        )
        review = service.review_task(
            step,
            actor="operator",
            approved=True,
            approval_reference="operation-dispatch-review",
        )
        item = service.queue.claim("operation-hierarchy-lease", lease_seconds=30)
        dispatched = service.dispatch_reviewed(
            step,
            review,
            model_request=ModelRequest(
                task_id=task.task_id,
                messages=[{"role": "user", "content": "retry"}],
            ),
            lease_proof=item.lease_proof,
            provider_binding_id="fake:default",
        )

        assert dispatched.phase == "dispatch"
        assert dispatched.transition.task.status is TaskStatus.RUNNING

        completed = service.evaluate_task(
            EvaluationEvidence(
                task_id=task.task_id,
                attempt=2,
                max_attempts=3,
                objective_met=True,
                deterministic_checks_passed=True,
                policy_compliant=True,
                external_outcome_known=True,
                retryable_failure=False,
                alternate_provider_available=False,
                human_approval_required=False,
            ),
            max_cycles=3,
            lease_proof=item.lease_proof,
        )
        assert completed.transition.task.status is TaskStatus.COMPLETED
        service.queue.complete(
            task.task_id,
            worker_id=item.lease_owner,
            state_version=item.state_version,
            execution_attempt=True,
        )

    status = OperationService.read_status(config, task.task_id)
    assert status["state"] == TaskStatus.COMPLETED.value
    assert status["queue_state"] == "completed"
