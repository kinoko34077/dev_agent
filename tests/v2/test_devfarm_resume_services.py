from __future__ import annotations

from pathlib import Path

import scripts.devfarm_resume as resume


def test_providers_for_resume_loads_plan_and_uses_assigned_provider_composition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observed: dict[str, object] = {}

    class FakeStore:
        def __init__(self, root: Path) -> None:
            observed["root"] = root

        def load(self, run_id: str) -> dict[str, object]:
            observed["run_id"] = run_id
            return {"run_id": run_id, "tasks": []}

    def fake_build(plan, timeout_seconds, *, provider_builder):
        observed["plan"] = plan
        observed["timeout_seconds"] = timeout_seconds
        observed["provider_builder"] = provider_builder
        return {"assigned": True}

    monkeypatch.setattr(resume, "CommanderPlanStore", FakeStore)
    monkeypatch.setattr(resume, "build_assigned_providers", fake_build)

    result = resume.providers_for_resume(tmp_path, "run-1", 12.5)

    assert result == {"assigned": True}
    assert observed["root"] == tmp_path
    assert observed["run_id"] == "run-1"
    assert observed["timeout_seconds"] == 12.5
    assert callable(observed["provider_builder"])
