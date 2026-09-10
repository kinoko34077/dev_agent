import pytest

from src.dev_agent.domain.protocol import RiskLevel, Task, TaskType
from src.dev_agent.intelligence.capabilities import (
    CapabilityClassificationError,
    classify_task_capabilities,
    execution_capabilities,
)
from src.dev_agent.operation import OperationConfig, OperationError, OperationService
from src.dev_agent.resources.ledger import ResourceLedger


def test_task_competency_and_policy_traits_do_not_become_provider_capabilities():
    task = Task(
        objective="review a protected architecture change",
        task_type=TaskType.WORKER,
        required_capabilities=["architecture", "security", "text", "tool_call"],
        risk=RiskLevel.HIGH,
    )

    assert classify_task_capabilities(task.required_capabilities) == {
        "architecture": "competency",
        "security": "policy_trait",
        "text": "execution",
        "tool_call": "execution",
    }
    assert execution_capabilities(task.required_capabilities) == ("text", "tool_call")


def test_unknown_task_capability_fails_fast():
    with pytest.raises(CapabilityClassificationError, match="unknown task capability"):
        classify_task_capabilities(["text", "typo_tool_call"])


def test_operation_projects_local_resource_as_privacy_qualified(tmp_path):
    config = OperationConfig(
        data_dir=tmp_path,
        provider_id="ollama",
        model="qwen3:8b",
        provider_binding_id="ollama",
    )
    provider = type(
        "LocalProvider",
        (),
        {
            "provider_binding_id": "ollama",
            "model_id": "qwen3:8b",
            "intelligence_tier": None,
        },
    )()

    with ResourceLedger(tmp_path / "resources.sqlite3") as ledger:
        OperationService._ensure_resource(ledger, provider, config)
        resource = ledger.get_resource("ollama")

    assert resource["sensitivity"] == "sensitive"
    assert resource["metadata"]["privacy_profile"] == "local_only"


def test_child_submission_cannot_declassify_parent(tmp_path):
    config = OperationConfig(data_dir=tmp_path)
    parent = OperationService.submit(config, "handle private data", sensitivity="sensitive")

    with pytest.raises(OperationError, match="sensitivity"):
        OperationService.submit_child(config, parent.task_id, "send it to cloud", sensitivity="public")
