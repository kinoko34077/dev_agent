# 追跡表（Phase 0 ベースライン）

| ID | 要求 / 不変条件 | 実装領域 | 試験 / Gate |
| --- | --- | --- | --- |
| INV-001, REQ-002 | Provider independence / offline kernel | `providers/fake`, `domain` | alpha0 FakeProvider |
| INV-002, REQ-005 | bounded execution | `runtime/limits` | limit / fault tests |
| INV-003, INV-004, REQ-003, REQ-006 | explicit durable state | `state`, `domain` | serialization / resume |
| INV-005 | auditability | `state/events` | event trace |
| INV-008, INV-013 | protocol / rescue boundary | `providers`, `recovery` | import / adapter contract |
| INV-009, INV-012 | reversible change / human approval | `policy`, Git workflow | policy Gate |
| INV-010, INV-011 | hard budget / reserve | `resources` | survival Gate |
| INV-010, INV-011 | native-unit ledger, reservation, and reconciliation | `src/dev_agent/resources/ledger.py` (facade), `budget.py`, `budget_store.py`, `control.py`, `catalog.py`, `observations.py`, `health.py` | `tests/v2/test_resource_migrations.py`, `test_resource_observations.py`, `test_budget_reservations.py`, `test_phase6_budget_dispatch.py`, `test_phase6_provider_reconciliation.py` / F6A, F6E |
| INV-012 | privacy-first routing and deterministic survival mode | `src/dev_agent/resources/router.py`, `survival.py` | `tests/v2/test_phase6_router.py` / F6B |
| INV-007, INV-008 | independent recovery operation boundary | `recovery/phase6_recovery.py`, `validate_resources.py` | `tests/v2/test_phase6_recovery.py` / F6C |
| INV-003, INV-004 | durable queue ownership and lease fencing | `src/dev_agent/scheduler/queue.py` | `tests/v2/test_phase6_scheduler.py` / F6D |
| INV-003, INV-004, INV-011 | waiting queue items are parked until explicit wake; ambiguous provider resume is fail-closed | `src/dev_agent/scheduler/worker.py`, `src/dev_agent/runtime/controller.py` | `tests/v2/test_phase6_scheduler.py::test_worker_defers_waiting_task_until_explicit_wake`, `tests/v2/test_phase6_provider_reconciliation.py::test_paid_provider_timeout_waits_for_reconciliation_instead_of_failing` / G6O4 |
| INV-015 | workflow first | `workflows` | resolver Gate |
| INV-011, REQ-006 | Provider intent, replay, and durable provider audit ownership | `src/dev_agent/providers/journal.py`, `dispatch.py` (facade), `registry.py` | provider dispatch / replay / audit tests / G6O2 |
| INV-012 | Provider health mutation through the control-plane boundary | `src/dev_agent/resources/health.py`, `control.py`, `router.py` (read view) | provider health and routing tests / G6O2 |
| INV-009, INV-012 | DevFarm outbound scope, worktree isolation, patch validation, and host verification | `scripts/devfarm.py`, `scripts/devfarm_worker.py` | DevFarm manifest/result/patch/isolation tests |
| REQ-015 | Deterministic host-evidence evaluation with durable decision record | `src/dev_agent/intelligence/evaluator.py` | `tests/v2/test_evaluator.py` / Phase 7C foundation |
| REQ-017 | Evaluation evidence is durably recorded before a finite retry/escalation plan is returned | `src/dev_agent/intelligence/coordination.py` | `tests/v2/test_intelligence_coordination.py` / Phase 7D bounded coordination |
| REQ-018 | Explicit opt-in intelligence tier routing with exact resource-tier matching; default routing remains unchanged | `src/dev_agent/intelligence/routing.py`, `src/dev_agent/resources/router.py`, `src/dev_agent/providers/dispatch.py`, `src/dev_agent/runtime/controller.py` | `tests/v2/test_intelligence_routing.py` / Phase 7A-B bounded routing |
| REQ-019 | Host/operator must explicitly review a generated escalation plan; accepted/rejected review is durable, while dispatch and task mutation remain separate | `src/dev_agent/intelligence/coordination.py`, `src/dev_agent/intelligence/evaluator.py` | `tests/v2/test_intelligence_coordination.py` / Phase 7D plan review boundary |
| REQ-020 | An accepted plan may become a durable dispatch-ready handoff without provider selection, task mutation, or execution | `src/dev_agent/intelligence/coordination.py`, `src/dev_agent/intelligence/escalation.py`, `src/dev_agent/intelligence/evaluator.py` | `tests/v2/test_intelligence_coordination.py` / Phase 7D reviewed handoff boundary |
| REQ-016 | Refactor-preserving execution boundaries | `src/dev_agent/runtime/model_turn.py`, `runtime/legacy_provider.py`, `src/dev_agent/state/schema.py`, `state/core_repository.py`, `state/effects_repository.py`, `src/dev_agent/tools/executor.py`, `tools/effect_guard.py` | `tests/v2/test_provider_dispatch.py`, `test_tool_isolation.py`, `test_recovery_sqlite.py` / Refactor Freeze |
