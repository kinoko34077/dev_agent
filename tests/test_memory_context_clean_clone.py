"""Clean-clone regression for MemoryContext (#24 / #25 follow-up).

These tests use the production file-I/O path: MemoryContext -> utils.fileio.
read_file -> secure_open -> secure_check.check_permission under the
repository's own config (safe_mode: strict) and access rules. Nothing on that
path is monkeypatched.

Scope note: memory/__init__.py eagerly imports MemoryManager, which has a
separate, pre-existing circular import through core.executor. That legacy
defect is tracked separately; here only the package __init__ is skipped so
the concrete memory_loader / memory_context_initializer modules are imported
unchanged. This is therefore not a test of `import memory`.
"""

import shutil
import sys
import types
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MEMORY_DIR = _REPO_ROOT / "memory"
_CONTEXT_DIR = _MEMORY_DIR / "context"
_CONTEXT_FILES = ("system_prompt.txt", "config_snapshot.json", "summary_combined.txt", "memory_meta.json")


@pytest.fixture(autouse=True)
def memory_modules_without_package_init(monkeypatch):
    package = types.ModuleType("memory")
    package.__path__ = [str(_MEMORY_DIR)]
    monkeypatch.setitem(sys.modules, "memory", package)
    for name in ("memory.memory_loader", "memory.memory_context_initializer"):
        monkeypatch.delitem(sys.modules, name, raising=False)


@pytest.fixture
def clean_clone_context(tmp_path, monkeypatch):
    """Run from the repository root with memory/context/ absent, as after a clone.

    memory/context/ is untracked (#24). A developer checkout may already hold
    local runtime data there, so it is moved aside and restored afterwards.
    """
    monkeypatch.chdir(_REPO_ROOT)
    backup = tmp_path / "context-backup"
    if _CONTEXT_DIR.exists():
        shutil.move(str(_CONTEXT_DIR), str(backup))
    try:
        yield _CONTEXT_DIR
    finally:
        if _CONTEXT_DIR.exists():
            shutil.rmtree(_CONTEXT_DIR)
        if backup.exists():
            shutil.move(str(backup), str(_CONTEXT_DIR))


def test_memory_context_initializes_and_loads_through_real_file_io(clean_clone_context):
    from memory.memory_loader import MemoryContext

    assert not clean_clone_context.exists()

    context = MemoryContext()

    for name in _CONTEXT_FILES:
        assert (clean_clone_context / name).is_file()
    assert context.system_prompt  # loaded through read_file, not the fallback
    assert isinstance(context.config_snapshot, dict) and context.config_snapshot
    assert isinstance(context.meta, dict) and context.meta


def test_memory_context_keeps_existing_files(clean_clone_context):
    from memory.memory_loader import MemoryContext

    clean_clone_context.mkdir(parents=True)
    (clean_clone_context / "system_prompt.txt").write_text("custom prompt", encoding="utf-8")

    assert MemoryContext().get_system_prompt() == "custom prompt"


def test_read_file_permission_is_independent_of_cwd(clean_clone_context, tmp_path, monkeypatch):
    from memory.memory_loader import MemoryContext
    from utils.fileio import read_file

    MemoryContext()
    monkeypatch.chdir(tmp_path)

    assert read_file(str(clean_clone_context / "system_prompt.txt"), "txt")
