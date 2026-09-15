from __future__ import annotations

from pathlib import Path

import pytest

from scripts.devfarm import DevFarmError
from scripts.devfarm_repository import read_bounded_json, repository_path


def test_repository_path_keeps_development_artifacts_under_the_requested_parent(tmp_path: Path):
    root = tmp_path.resolve()

    assert repository_path(root, ".devfarm/tasks/task.json", required_parent=".devfarm/tasks") == (
        root / ".devfarm" / "tasks" / "task.json"
    )


@pytest.mark.parametrize(
    "relative",
    ["../outside.json", ".devfarm/../outside.json", "C:/outside.json"],
)
def test_repository_path_rejects_escape(relative: str, tmp_path: Path):
    with pytest.raises(DevFarmError):
        repository_path(tmp_path, relative, required_parent=".devfarm/tasks")


def test_read_bounded_json_enforces_repository_containment_and_size(tmp_path: Path):
    payload = tmp_path / ".devfarm" / "self-improvement" / "observation.json"
    payload.parent.mkdir(parents=True)
    payload.write_text('{"status": "OBSERVED"}', encoding="utf-8")

    assert read_bounded_json(tmp_path, payload, maximum_chars=64) == {"status": "OBSERVED"}

    with pytest.raises(DevFarmError, match="exceeds"):
        read_bounded_json(tmp_path, payload, maximum_chars=4)

    with pytest.raises(DevFarmError, match="escapes repository"):
        read_bounded_json(tmp_path, "../outside.json")
