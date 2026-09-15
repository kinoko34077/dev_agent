import json

from scripts.devfarm_plan_ownership import load_ownership_projections


def test_ownership_projection_is_read_only_and_keeps_active_plan_identity(tmp_path):
    plans = tmp_path / ".devfarm" / "plans"
    plans.mkdir(parents=True)
    (plans / "ownership-query.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": "ownership-query",
                "objective": "read ownership",
                "base_revision": "abc123",
                "tasks": [
                    {
                        "task_id": "task-a",
                        "owner": "worker",
                        "status": "READY",
                        "manifest_path": ".devfarm/tasks/task-a.json",
                        "ownership": ["src/dev_agent/coordination/work.py"],
                        "assignment": {
                            "provider_id": "cloudflare",
                            "model_id": "@cf/meta/llama-3.1-8b-instruct",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    projections = load_ownership_projections(tmp_path, plans)

    assert projections == [
        {
            "run_id": "ownership-query",
            "tasks": [
                {
                    "task_id": "task-a",
                    "owner": "worker",
                    "status": "READY",
                    "ownership": ["src/dev_agent/coordination/work.py"],
                }
            ],
        }
    ]
    assert (plans / "ownership-query.json").read_text(encoding="utf-8").count("task-a") == 2


def test_input_plan_spec_is_not_treated_as_active_ownership(tmp_path):
    plans = tmp_path / ".devfarm" / "plans"
    plans.mkdir(parents=True)
    plan = {
        "schema_version": 1,
        "run_id": "input-only",
        "objective": "input specification",
        "base_revision": "abc123",
        "tasks": [
            {
                "task_id": "task-a",
                "owner": "worker",
                "ownership": ["src/dev_agent/resources/model_catalog.py"],
                "manifest_path": ".devfarm/tasks/task-a.json",
                "assignment": {
                    "provider_id": "cloudflare",
                    "model_id": "@cf/meta/llama-3.1-8b-instruct",
                },
            }
        ],
    }
    (plans / "input-only.input.json").write_text(json.dumps(plan), encoding="utf-8")

    assert load_ownership_projections(tmp_path, plans) == []
