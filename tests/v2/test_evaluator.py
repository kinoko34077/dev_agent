import pytest

from src.dev_agent.intelligence.evaluator import EvaluationEvidence, EvaluatorDecision, TaskEvaluator


def _evidence(**overrides):
    values = {
        "task_id": "task-1",
        "attempt": 1,
        "max_attempts": 3,
        "objective_met": True,
        "deterministic_checks_passed": True,
        "policy_compliant": True,
        "external_outcome_known": True,
        "retryable_failure": False,
        "alternate_provider_available": False,
        "human_approval_required": False,
    }
    values.update(overrides)
    return EvaluationEvidence(**values)


def test_evaluator_passes_only_when_objective_and_deterministic_checks_are_verified():
    result = TaskEvaluator().evaluate(_evidence())

    assert result.decision is EvaluatorDecision.PASS
    assert result.task_id == "task-1"
    assert "deterministic_checks_passed" in result.reasons


def test_evaluator_uses_another_provider_before_same_provider_retry():
    result = TaskEvaluator().evaluate(
        _evidence(
            objective_met=False,
            deterministic_checks_passed=False,
            retryable_failure=True,
            alternate_provider_available=True,
        )
    )

    assert result.decision is EvaluatorDecision.RETRY_OTHER_PROVIDER


def test_evaluator_reconciles_unknown_external_outcome_instead_of_retrying():
    result = TaskEvaluator().evaluate(_evidence(external_outcome_known=False, retryable_failure=True))

    assert result.decision is EvaluatorDecision.WAIT_HUMAN
    assert "external_outcome_unknown" in result.reasons


def test_evaluator_escalates_non_retryable_failure_until_attempt_ceiling_then_fails():
    retry = TaskEvaluator().evaluate(_evidence(objective_met=False, deterministic_checks_passed=False))
    terminal = TaskEvaluator().evaluate(_evidence(attempt=3, objective_met=False, deterministic_checks_passed=False))

    assert retry.decision is EvaluatorDecision.ESCALATE
    assert terminal.decision is EvaluatorDecision.FAIL


def test_evaluator_does_not_pass_without_deterministic_evidence_or_policy_compliance():
    uncertain = TaskEvaluator().evaluate(_evidence(deterministic_checks_passed=None))
    policy_violation = TaskEvaluator().evaluate(_evidence(policy_compliant=False))

    assert uncertain.decision is EvaluatorDecision.ESCALATE
    assert policy_violation.decision is EvaluatorDecision.WAIT_HUMAN


def test_evaluator_rejects_invalid_attempt_bounds():
    with pytest.raises(ValueError, match="attempt"):
        TaskEvaluator().evaluate(_evidence(attempt=0))
    with pytest.raises(ValueError, match="max_attempts"):
        TaskEvaluator().evaluate(_evidence(max_attempts=0))
