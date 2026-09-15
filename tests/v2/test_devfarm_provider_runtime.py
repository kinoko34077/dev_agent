from __future__ import annotations

from scripts.devfarm_provider_runtime import build_assigned_providers


def test_build_assigned_providers_materializes_only_resumable_worker_tasks() -> None:
    calls: list[tuple[str, str, float, str | None]] = []

    def builder(name: str, model: str, timeout: float, binding: str | None = None) -> object:
        calls.append((name, model, timeout, binding))
        return object()

    plan = {
        "tasks": [
            {
                "task_id": "worker-ready",
                "owner": "worker",
                "status": "READY",
                "assignment": {
                    "provider_id": "gemini",
                    "model_id": "gemini-3.6-flash",
                    "provider_binding_id": "gemini:worker:free-3",
                },
            },
            {
                "task_id": "worker-dispatched",
                "owner": "worker",
                "status": "DISPATCHED",
                "assignment": {
                    "provider_id": "gemini",
                    "model_id": "gemini-3.6-flash",
                },
            },
            {
                "task_id": "codex-ready",
                "owner": "codex",
                "status": "READY",
                "assignment": {},
            },
        ]
    }

    providers = build_assigned_providers(plan, 30.0, provider_builder=builder)

    assert set(providers) == {"worker-ready"}
    assert calls == [("gemini", "gemini-3.6-flash", 30.0, "gemini:worker:free-3")]
