import sys
import types
from pathlib import Path

import pytest

_MEMORY_DIR = Path(__file__).resolve().parents[1] / "memory"


@pytest.fixture(autouse=True)
def memory_package_without_init(monkeypatch):
    """Import memory submodules without executing memory/__init__.py.

    The package __init__ eagerly imports MemoryManager, which has a separate
    pre-existing circular import through core.executor. That cycle is out of
    scope here; these tests only cover MemoryContext on a clean clone.
    """
    package = types.ModuleType("memory")
    package.__path__ = [str(_MEMORY_DIR)]
    monkeypatch.setitem(sys.modules, "memory", package)
    for name in ("memory.memory_loader", "memory.memory_context_initializer"):
        monkeypatch.delitem(sys.modules, name, raising=False)


def _plain_read(path, mode):
    import json

    with open(path, encoding="utf-8") as handle:
        return json.load(handle) if mode == "json" else handle.read()


@pytest.fixture
def clean_cwd(tmp_path, monkeypatch):
    """Simulate a clean clone: memory/context/ is untracked (#24) and absent.

    utils.fileio.read_file is replaced because its permission path
    normalization is independently broken (it resolves against cwd/..); that
    pre-existing defect is out of scope for the clean-clone contract.
    """
    monkeypatch.chdir(tmp_path)
    import memory.memory_loader as loader

    monkeypatch.setattr(loader, "read_file", _plain_read)
    return tmp_path


def test_memory_context_initializes_missing_files_on_clean_clone(clean_cwd):
    from memory.memory_loader import MemoryContext

    assert not (clean_cwd / "memory" / "context").exists()

    context = MemoryContext()

    for name in ("system_prompt.txt", "config_snapshot.json", "summary_combined.txt", "memory_meta.json"):
        assert (clean_cwd / "memory" / "context" / name).is_file()
    assert context.get_system_prompt()
    assert isinstance(context.get_config_snapshot(), dict)
    assert isinstance(context.get_meta(), dict)


def test_memory_context_does_not_overwrite_existing_files(clean_cwd):
    from memory.memory_loader import MemoryContext

    context_dir = clean_cwd / "memory" / "context"
    context_dir.mkdir(parents=True)
    (context_dir / "system_prompt.txt").write_text("custom prompt", encoding="utf-8")

    assert MemoryContext().get_system_prompt() == "custom prompt"
