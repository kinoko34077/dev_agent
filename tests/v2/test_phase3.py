from pathlib import Path

import pytest

from src.dev_agent.domain.protocol import Task, ToolCall
from src.dev_agent.policy import ApprovalPolicy, PathPolicy
from src.dev_agent.runtime import TaskGraph, TaskGraphError
from src.dev_agent.providers.fake import FakeProvider
from src.dev_agent.runtime import Controller
from src.dev_agent.state import SQLiteStateStore
from src.dev_agent.tools import ToolRegistry, ToolRuntime, ToolSpec


def test_sqlite_store_survives_reopen(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    task = Task(objective="persist me")
    with SQLiteStateStore(path) as store:
        store.save_task(task)
    with SQLiteStateStore(path) as reopened:
        loaded = reopened.load_task(task.task_id)
        assert loaded is not None
        assert loaded.objective == "persist me"


def test_task_graph_enforces_depth_and_child_limits():
    graph = TaskGraph(max_depth=1, max_children_per_task=1)
    root = Task(objective="root")
    graph.add(root)
    graph.add_child(root.task_id, Task(objective="child"))
    with pytest.raises(TaskGraphError, match="child"):
        graph.add_child(root.task_id, Task(objective="second"))
    with pytest.raises(TaskGraphError, match="depth"):
        graph.add_child(next(iter(graph.children[root.task_id])), Task(objective="too deep"))
    child = graph.tasks[next(iter(graph.children[root.task_id]))]
    root.parent_task_id = child.task_id
    with pytest.raises(TaskGraphError, match="cycle"):
        graph.validate()


def test_controller_resumes_task_from_reopened_sqlite_store(tmp_path):
    path = tmp_path / "resume.sqlite3"
    task = Task(objective="resume me")
    with SQLiteStateStore(path) as store:
        store.save_task(task)
    registry = ToolRegistry()
    registry.register(ToolSpec(name="echo", description="test", required_arguments=frozenset({"value"}), handler=lambda args: args))
    with SQLiteStateStore(path) as reopened:
        controller = Controller(FakeProvider(tool_arguments={"value": "resumed"}), ToolRuntime(registry), reopened)
        assert controller.resume(task.task_id).status.value == "completed"


def test_path_policy_denies_escape_and_symlink(tmp_path):
    workspace = tmp_path / "workspace"
    allowed = workspace / "sandbox"
    outside = tmp_path / "outside"
    allowed.mkdir(parents=True)
    outside.mkdir()
    link = allowed / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    policy = PathPolicy(workspace, {"sandbox": {"read", "write"}})
    assert policy.check("sandbox/file.txt", "write")
    assert not policy.check("../outside/file.txt", "write")
    assert not policy.check(link / "secret.txt", "read")


def test_path_policy_rejects_a_path_that_resolves_outside(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "sandbox").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    policy = PathPolicy(workspace, {"sandbox": {"read", "write"}})
    original_resolve = Path.resolve

    def resolve_with_virtual_link(path, strict=False):
        if "virtual-link" in path.parts:
            return outside / "secret.txt"
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_with_virtual_link)
    assert not policy.check(workspace / "sandbox" / "virtual-link" / "secret.txt", "read")


def test_idempotency_prevents_duplicate_handler_after_reopen(tmp_path):
    path = tmp_path / "state.sqlite3"
    calls = []
    registry = ToolRegistry()
    registry.register(ToolSpec(name="charge", description="test", handler=lambda args: calls.append(args) or {"ok": True}))
    with SQLiteStateStore(path) as store:
        runtime = ToolRuntime(registry).with_result_store(store)
        call = ToolCall(tool_name="charge", idempotency_key="payment-1")
        runtime.execute(call)
    with SQLiteStateStore(path) as reopened:
        runtime = ToolRuntime(registry).with_result_store(reopened)
        runtime.execute(ToolCall(tool_name="charge", idempotency_key="payment-1"))
    assert calls == [{}]


def test_approval_defaults_to_deny_for_high_risk_side_effects():
    policy = ApprovalPolicy()
    assert not policy.authorize("financial")
    assert policy.authorize("financial", approved=True)
    assert policy.authorize("local_read")


def test_task_graph_reconstructs_from_durable_tasks():
    root = Task(objective="root")
    child = Task(objective="child", parent_task_id=root.task_id, root_task_id=root.task_id, depth=1)
    graph = TaskGraph.from_tasks([child, root], max_total_tasks_per_root=2)
    assert graph.descendants(root.task_id) == [child.task_id]
