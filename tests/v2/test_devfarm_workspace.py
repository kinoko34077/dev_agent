from scripts import devfarm_workspace
from scripts.devfarm import init_farm, prepare_worktree, write_manifest, write_result
from types import SimpleNamespace


def test_workspace_operations_are_exposed_by_the_standalone_boundary():
    assert init_farm is devfarm_workspace.init_farm
    assert prepare_worktree is devfarm_workspace.prepare_worktree
    assert write_manifest is devfarm_workspace.write_manifest
    assert write_result is devfarm_workspace.write_result


def test_prepare_worktree_falls_back_to_detached_tree_for_ref_lock_permission(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if "--detach" not in command:
            return SimpleNamespace(
                returncode=1,
                stderr="fatal: cannot lock ref 'refs/heads/agent/devfarm/task-1.lock': Permission denied",
            )
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(devfarm_workspace.subprocess, "run", fake_run)

    worktree = prepare_worktree(
        tmp_path,
        task_id="task-1",
        branch="agent/devfarm/task-1",
        revision="abc123",
    )

    assert worktree == tmp_path / ".devfarm" / "worktrees" / "task-1"
    assert len(commands) == 2
    assert "-b" in commands[0]
    assert "--detach" in commands[1]
    assert commands[1][-1] == "abc123"


def test_prepare_worktree_falls_back_to_shared_clone_when_git_worktrees_are_denied(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if "bundle" in command:
            return SimpleNamespace(returncode=0, stderr="")
        if "clone" in command:
            return SimpleNamespace(returncode=0, stderr="")
        if "checkout" in command:
            return SimpleNamespace(returncode=0, stderr="")
        if "--detach" in command:
            return SimpleNamespace(
                returncode=1,
                stderr="fatal: could not create directory of '.git/worktrees/task-2': Permission denied",
            )
        return SimpleNamespace(
            returncode=1,
            stderr="fatal: cannot lock ref 'refs/heads/agent/devfarm/task-2.lock': Permission denied",
        )

    monkeypatch.setattr(devfarm_workspace.subprocess, "run", fake_run)

    worktree = prepare_worktree(
        tmp_path,
        task_id="task-2",
        branch="agent/devfarm/task-2",
        revision="abc123",
    )

    assert worktree == tmp_path / ".devfarm" / "worktrees" / "task-2"
    assert len(commands) == 5
    assert "bundle" in commands[2]
    assert commands[2][-1] == "HEAD"
    assert "--no-checkout" in commands[3]
    assert commands[4][-1] == "abc123"
