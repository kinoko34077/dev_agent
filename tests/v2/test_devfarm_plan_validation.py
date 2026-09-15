from scripts.devfarm_plan_validation import (
    OWNERS,
    PLAN_STATUSES,
    summarize_delegation,
    validate_plan,
)


def test_plan_validation_is_a_public_standalone_boundary():
    plan = validate_plan(
        {
            "run_id": "standalone-validation",
            "objective": "validate a narrow plan",
            "base_revision": "abc123",
            "tasks": [
                {
                    "task_id": "task-a",
                    "owner": "codex",
                    "ownership": ["docs/plan.md"],
                }
            ],
        }
    )

    assert plan["status"] == "PLANNED"
    assert plan["tasks"][0]["work_address"] == "1"
    assert summarize_delegation(plan)["codex_owned_task_count"] == 1
    assert "codex" in OWNERS
    assert "PLANNED" in PLAN_STATUSES
