"""Small affected-test lookup for fast local feedback.

This is an advisory map, not a replacement for the full v2 regression.
"""

from __future__ import annotations

import fnmatch
import json
import sys


TEST_SCOPE_MAP: dict[str, tuple[str, ...]] = {
    "src/dev_agent/resources/**": (
        "tests/v2/test_resource_control.py",
        "tests/v2/test_resource_observations.py",
        "tests/v2/test_resource_migrations.py",
        "tests/v2/test_budget_reservations.py",
        "tests/v2/test_quota_scheduler.py",
    ),
    "src/dev_agent/providers/**": (
        "tests/v2/test_provider_contracts.py",
        "tests/v2/test_provider_factory.py",
        "tests/v2/test_provider_dispatch.py",
        "tests/v2/test_provider_exports.py",
        "tests/v2/test_provider_transcript.py",
        "tests/v2/test_phase6_provider_dispatch.py",
        "tests/v2/test_phase6_provider_fencing.py",
        "tests/v2/test_phase6_provider_reconciliation.py",
    ),
    "src/dev_agent/intelligence/**": (
        "tests/v2/test_intelligence.py",
        "tests/v2/test_intelligence_routing.py",
        "tests/v2/test_intelligence_execution.py",
        "tests/v2/test_intelligence_coordination.py",
        "tests/v2/test_intelligence_dispatch_loop.py",
        "tests/v2/test_intelligence_lifecycle_loop.py",
        "tests/v2/test_planner_privacy_boundaries.py",
    ),
    "src/dev_agent/runtime/controller.py": (
        "tests/v2/test_phase3.py",
        "tests/v2/test_controller_refactor.py",
        "tests/v2/test_phase6_provider_dispatch.py",
        "tests/v2/test_phase6_provider_reconciliation.py",
        "tests/v2/test_phase6_provider_fencing.py",
        "tests/v2/test_approval_boundary.py",
    ),
    "src/dev_agent/runtime/**": (
        "tests/v2/test_phase3.py",
        "tests/v2/test_phase6_provider_dispatch.py",
        "tests/v2/test_phase6_provider_reconciliation.py",
        "tests/v2/test_phase6_provider_terminal_states.py",
    ),
    "src/dev_agent/state/**": (
        "tests/v2/test_phase6_scheduler.py",
        "tests/v2/test_phase6_recovery.py",
        "tests/v2/test_recovery_sqlite.py",
        "tests/v2/test_effect_reconciliation.py",
    ),
    "src/dev_agent/scheduler/**": (
        "tests/v2/test_phase6_scheduler.py",
        "tests/v2/test_quota_scheduler.py",
        "tests/v2/test_phase6_recovery.py",
    ),
    "src/dev_agent/operation*.py": (
        "tests/v2/test_operation.py",
        "tests/v2/test_operation_boundaries.py",
        "tests/v2/test_operation_hierarchy_e2e.py",
        "tests/v2/test_planner_privacy_boundaries.py",
    ),
    "scripts/devfarm*.py": (
        "tests/v2/test_devfarm.py",
        "tests/v2/test_devfarm_manifest.py",
        "tests/v2/test_devfarm_patch_validation.py",
        "tests/v2/test_devfarm_orchestrator.py",
        "tests/v2/test_devfarm_commander.py",
        "tests/v2/test_devfarm_metrics.py",
    ),
}


def _normalize(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def affected_tests(changed_paths: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    selected: set[str] = set()
    for raw_path in changed_paths:
        path = _normalize(raw_path)
        if path.startswith("tests/v2/") and path.endswith(".py"):
            selected.add(path)
        for pattern, tests in TEST_SCOPE_MAP.items():
            if fnmatch.fnmatch(path, pattern):
                selected.update(tests)
    return tuple(sorted(selected))


def main(argv: list[str] | None = None) -> int:
    paths = list(argv if argv is not None else sys.argv[1:])
    if not paths:
        print("usage: python scripts/test_scope.py <changed-path> [...]")
        return 2
    print(json.dumps({"changed_paths": [_normalize(path) for path in paths], "tests": list(affected_tests(paths))}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
