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


def test_providers_for_resume_passes_explicit_local_trial_to_existing_provider_admission(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []
    provider = object()

    class FakeStore:
        def __init__(self, _root: Path) -> None:
            pass

        def load(self, run_id: str) -> dict[str, object]:
            return {
                "run_id": run_id,
                "tasks": [
                    {
                        "task_id": "worker-1",
                        "owner": "worker",
                        "status": "READY",
                        "assignment": {
                            "provider_id": "ollama",
                            "model_id": "qwen3.5:9b",
                            "provider_binding_id": "ollama:local:qwen3.5-9b",
                        },
                    }
                ],
            }

    def capture_build(provider_id, model_id, timeout_seconds, binding_id, *, allow_local=False):
        calls.append((provider_id, model_id, timeout_seconds, binding_id, allow_local))
        return provider

    monkeypatch.setattr(resume, "CommanderPlanStore", FakeStore)
    monkeypatch.setattr(resume, "build_worker_provider", capture_build)

    providers = resume.providers_for_resume(tmp_path, "run-1", 12.5, local_trial=True)

    assert providers == {"worker-1": provider}
    assert calls == [("ollama", "qwen3.5:9b", 12.5, "ollama:local:qwen3.5-9b", True)]
