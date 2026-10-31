"""Synthetic-only regression: Discord-selected paths must not alias protected files."""

from pathlib import Path
import os

import pytest

from src.dev_agent.discord.adapter import DiscordScope
from src.dev_agent.security.protected_paths import PathProtectionClass, classify_path


def _symlink(source: Path, destination: Path, *, directory: bool = False) -> None:
    try:
        os.symlink(source, destination, target_is_directory=directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation unavailable in test environment: {type(exc).__name__}")


def test_protected_file_symlink_alias_is_rejected(tmp_path):
    protected = tmp_path / ".git" / "config"
    protected.parent.mkdir()
    protected.write_text("synthetic-no-secret=1", encoding="utf-8")
    assert classify_path(".git/config") is PathProtectionClass.HARD_DENY
    _symlink(protected, tmp_path / "ordinary-looking.txt")

    scope = DiscordScope(tmp_path)
    with pytest.raises(ValueError):
        scope.resolve_file("ordinary-looking.txt")


def test_protected_directory_symlink_alias_is_rejected(tmp_path):
    protected = tmp_path / "recovery"
    protected.mkdir()
    (protected / "synthetic.txt").write_text("synthetic-only", encoding="utf-8")
    _symlink(protected, tmp_path / "looks-normal", directory=True)

    scope = DiscordScope(tmp_path)
    with pytest.raises(ValueError):
        scope.resolve_directory("looks-normal")
    with pytest.raises(ValueError):
        scope.resolve_file("looks-normal/synthetic.txt")


def test_normal_symlink_is_rejected_to_prevent_retarget_after_check(tmp_path):
    normal = tmp_path / "normal.txt"
    normal.write_text("synthetic-only", encoding="utf-8")
    _symlink(normal, tmp_path / "alias.txt")

    with pytest.raises(ValueError):
        DiscordScope(tmp_path).resolve_file("alias.txt")


def test_normal_selected_file_and_directory_are_unchanged(tmp_path):
    normal = tmp_path / "docs"
    normal.mkdir()
    (normal / "readme.txt").write_text("synthetic-only", encoding="utf-8")

    scope = DiscordScope(tmp_path)
    assert scope.resolve_directory("docs") == "docs"
    assert scope.resolve_file("docs/readme.txt") == "docs/readme.txt"
    with pytest.raises(ValueError):
        scope.resolve_directory(".git")
    with pytest.raises(ValueError):
        scope.resolve_file("recovery/anything.txt")
