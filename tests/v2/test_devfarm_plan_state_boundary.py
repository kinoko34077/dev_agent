from __future__ import annotations

import ast
from pathlib import Path


def test_integration_uses_plan_state_boundary_instead_of_commander_module():
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / "scripts" / "devfarm_integration.py").read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "scripts.devfarm_plan_state" in imported_modules
    assert "scripts.devfarm_commander" not in imported_modules


def test_commander_keeps_compatibility_exports_for_plan_state_boundary():
    from scripts import devfarm_commander, devfarm_plan_state

    assert devfarm_commander.CommanderPlanStore is devfarm_plan_state.CommanderPlanStore
    assert devfarm_commander.PlanConflictError is devfarm_plan_state.PlanConflictError
    assert devfarm_commander.refresh_plan is devfarm_plan_state.refresh_plan
    assert devfarm_commander.record_result is devfarm_plan_state.record_result
