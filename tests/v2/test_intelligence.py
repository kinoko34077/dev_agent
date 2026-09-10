import pytest

from src.dev_agent.domain.protocol import (
    ProtocolError,
    RecoveryTaskAuthority,
    RiskLevel,
    Task,
    TaskType,
    IntelligenceTier,
)
from src.dev_agent.intelligence import TaskIntelligencePolicy
from src.dev_agent.providers.fake import FakeProvider
from src.dev_agent.runtime import Controller
from src.dev_agent.state import JsonStateStore
from src.dev_agent.tools import ToolRegistry, ToolRuntime, ToolSpec


def test_task_profile_round_trips_and_legacy_tasks_get_safe_defaults():
    task = Task(
        objective="design a change",
        task_type=TaskType.REASONING,
        required_capabilities=["architecture", "text"],
        risk=RiskLevel.HIGH,
    )

    restored = Task.from_dict(task.to_dict())

    assert restored.to_dict() == task.to_dict()
    assert restored.task_type is TaskType.REASONING
    assert restored.required_capabilities == ["architecture", "text"]
    assert restored.risk is RiskLevel.HIGH

    legacy = Task.from_dict({"objective": "old task"})
    assert legacy.task_type is TaskType.WORKER
    assert legacy.required_capabilities == []
    assert legacy.risk is RiskLevel.NORMAL


def test_task_profile_validates_values_and_recovery_authority_sets_recovery_type():
    with pytest.raises(ProtocolError, match="task_type"):
        Task(objective="bad type", task_type="unknown")  # type: ignore[arg-type]
    with pytest.raises(ProtocolError, match="required_capabilities"):
        Task(objective="bad capability", required_capabilities=[""])  # type: ignore[list-item]

    recovery = RecoveryTaskAuthority.create(objective="repair state")
    assert recovery.task_type is TaskType.RECOVERY


def test_intelligence_policy_maps_task_type_and_raises_for_risk_or_protected_capability():
    policy = TaskIntelligencePolicy()

    deterministic = policy.decide(Task(objective="format", task_type=TaskType.DETERMINISTIC))
    assert deterministic.minimum_tier is IntelligenceTier.L0
    assert deterministic.maximum_tier is IntelligenceTier.L0
    assert deterministic.allowed_tiers == (IntelligenceTier.L0,)

    worker = policy.decide(Task(objective="add tests", task_type=TaskType.WORKER))
    assert worker.minimum_tier is IntelligenceTier.L1
    assert worker.maximum_tier is IntelligenceTier.L1

    protected = policy.decide(
        Task(
            objective="change architecture",
            task_type=TaskType.WORKER,
            required_capabilities=["architecture"],
            risk=RiskLevel.HIGH,
        )
    )
    assert protected.minimum_tier is IntelligenceTier.L2
    assert protected.maximum_tier is IntelligenceTier.L2
    assert protected.requires_human_approval is False
    assert "required_capability:architecture" in protected.reasons
    assert "risk:high" in protected.reasons

    critical = policy.decide(Task(objective="critical change", task_type=TaskType.REASONING, risk=RiskLevel.CRITICAL))
    assert critical.minimum_tier is IntelligenceTier.L3
    assert critical.maximum_tier is IntelligenceTier.L3
    assert critical.requires_human_approval is True


def test_intelligence_policy_ignores_model_metadata_tier_and_does_not_self_elevate():
    task = Task(
        objective="write a test",
        task_type=TaskType.WORKER,
        metadata={"intelligence_tier": "L3", "selected_tier": "L3"},
    )

    decision = TaskIntelligencePolicy().decide(task)

    assert decision.minimum_tier is IntelligenceTier.L1
    assert decision.maximum_tier is IntelligenceTier.L1
    assert decision.allowed_tiers == (IntelligenceTier.L1,)


def test_controller_carries_task_profile_to_model_request_without_trusting_metadata(tmp_path):
    provider = FakeProvider(tool_arguments={"value": "ok"})
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="echo",
            description="echo",
            required_arguments=frozenset({"value"}),
            handler=lambda args: {"echo": args["value"]},
        )
    )
    task = Task(
        objective="add a test",
        task_type=TaskType.WORKER,
        required_capabilities=["text"],
        risk=RiskLevel.NORMAL,
        metadata={"intelligence_tier": "L3"},
    )

    store = JsonStateStore(tmp_path / "state.json")
    completed = Controller(provider, ToolRuntime(registry), store).run(task)

    assert completed.status.value == "completed"
    request = provider.requests[0]
    assert request.requested_capabilities == ["text"]
    assert request.metadata == {
        "task_type": "worker",
        "risk": "normal",
        "minimum_intelligence_tier": "L1",
        "maximum_intelligence_tier": "L1",
        "allowed_intelligence_tiers": ["L1"],
        "requires_human_approval": False,
        "intelligence_policy_reasons": ["task_type:worker", "risk:normal"],
        "task_context": {
            "type": "dev_agent.task_context.v1",
            "inputs": {},
            "constraints": {},
        },
    }
