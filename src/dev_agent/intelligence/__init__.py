"""Intelligence policy boundaries with lazy compatibility exports."""

from importlib import import_module

_LAZY_EXPORTS = {
    "IntelligenceDecision": (".policy", "IntelligenceDecision"),
    "TaskIntelligencePolicy": (".policy", "TaskIntelligencePolicy"),
    "EvaluationEvidence": (".evaluator", "EvaluationEvidence"),
    "EvaluationRecorder": (".evaluator", "EvaluationRecorder"),
    "EvaluationResult": (".evaluator", "EvaluationResult"),
    "EvaluatorDecision": (".evaluator", "EvaluatorDecision"),
    "TaskEvaluator": (".evaluator", "TaskEvaluator"),
    "BoundedEscalationPolicy": (".escalation", "BoundedEscalationPolicy"),
    "EscalationContext": (".escalation", "EscalationContext"),
    "EscalationDispatchRequest": (".escalation", "EscalationDispatchRequest"),
    "EscalationPlan": (".escalation", "EscalationPlan"),
    "EscalationTarget": (".escalation", "EscalationTarget"),
    "EvaluationCoordinator": (".coordination", "EvaluationCoordinator"),
    "EvaluationCycle": (".coordination", "EvaluationCycle"),
    "IntelligenceRoutePolicy": (".routing", "IntelligenceRoutePolicy"),
    "EscalationExecutionDenied": (".execution", "EscalationExecutionDenied"),
    "EscalationExecutionError": (".execution", "EscalationExecutionError"),
    "EscalationExecutionResult": (".execution", "EscalationExecutionResult"),
    "EscalationExecutionStatus": (".execution", "EscalationExecutionStatus"),
    "EscalationExecutor": (".execution", "EscalationExecutor"),
    "WorkflowPromotionCandidate": (".workflow", "WorkflowPromotionCandidate"),
    "WorkflowPromotionCoordinator": (".workflow", "WorkflowPromotionCoordinator"),
    "WorkflowPromotionCycle": (".workflow", "WorkflowPromotionCycle"),
    "WorkflowPromotionDecision": (".workflow", "WorkflowPromotionDecision"),
    "WorkflowPromotionEvidence": (".workflow", "WorkflowPromotionEvidence"),
    "WorkflowPromotionPolicy": (".workflow", "WorkflowPromotionPolicy"),
    "WorkflowPromotionRecorder": (".workflow", "WorkflowPromotionRecorder"),
    "EvaluationDispatchCoordinator": (".loop", "EvaluationDispatchCoordinator"),
    "EvaluationDispatchCycle": (".loop", "EvaluationDispatchCycle"),
    "EvaluationDispatchStatus": (".loop", "EvaluationDispatchStatus"),
    "TaskLifecycleCoordinator": (".lifecycle", "TaskLifecycleCoordinator"),
    "TaskLifecycleTransition": (".lifecycle", "TaskLifecycleTransition"),
    "FiniteLifecycleLoop": (".lifecycle_loop", "FiniteLifecycleLoop"),
    "LifecycleLimitExceeded": (".lifecycle_loop", "LifecycleLimitExceeded"),
    "LifecycleStep": (".lifecycle_loop", "LifecycleStep"),
    "ChildTaskProposal": (".planner", "ChildTaskProposal"),
    "PlanningValidationError": (".planner", "PlanningValidationError"),
    "RootPlanningProposal": (".planner", "RootPlanningProposal"),
    "RootPlanningValidator": (".planner", "RootPlanningValidator"),
    "EvidenceBasedRoutingPolicy": (".evidence_routing", "EvidenceBasedRoutingPolicy"),
    "EvidenceRouteDecision": (".evidence_routing", "EvidenceRouteDecision"),
    "EvidenceRoutingError": (".evidence_routing", "EvidenceRoutingError"),
    "ExecutionTarget": (".target", "ExecutionTarget"),
    "ExecutionTargetDecision": (".target", "ExecutionTargetDecision"),
    "ExecutionTargetError": (".target", "ExecutionTargetError"),
    "ExecutionTargetPolicy": (".target", "ExecutionTargetPolicy"),
    "CANONICAL_EXECUTION_CAPABILITIES": (".capabilities", "CANONICAL_EXECUTION_CAPABILITIES"),
    "TASK_COMPETENCIES": (".capabilities", "TASK_COMPETENCIES"),
    "TASK_POLICY_TRAITS": (".capabilities", "TASK_POLICY_TRAITS"),
    "CapabilityClassificationError": (".capabilities", "CapabilityClassificationError"),
    "classify_task_capabilities": (".capabilities", "classify_task_capabilities"),
    "execution_capabilities": (".capabilities", "execution_capabilities"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))


__all__ = list(_LAZY_EXPORTS)
