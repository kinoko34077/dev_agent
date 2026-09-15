from scripts import devfarm_workspace
from scripts.devfarm import init_farm, prepare_worktree, write_manifest, write_result


def test_workspace_operations_are_exposed_by_the_standalone_boundary():
    assert init_farm is devfarm_workspace.init_farm
    assert prepare_worktree is devfarm_workspace.prepare_worktree
    assert write_manifest is devfarm_workspace.write_manifest
    assert write_result is devfarm_workspace.write_result
