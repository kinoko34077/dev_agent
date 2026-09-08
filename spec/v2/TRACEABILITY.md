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
| INV-010, INV-011 | native-unit ledger, reservation, and reconciliation | `src/dev_agent/resources/ledger.py`, `budget.py`, `control.py` | `tests/v2/test_phase6_resources.py`, `test_phase6_integration.py` / F6A, F6E |
| INV-012 | privacy-first routing and deterministic survival mode | `src/dev_agent/resources/router.py`, `survival.py` | `tests/v2/test_phase6_router.py` / F6B |
| INV-007, INV-008 | independent recovery operation boundary | `recovery/phase6_recovery.py`, `validate_resources.py` | `tests/v2/test_phase6_recovery.py` / F6C |
| INV-003, INV-004 | durable queue ownership and lease fencing | `src/dev_agent/scheduler/queue.py` | `tests/v2/test_phase6_scheduler.py` / F6D |
| INV-003, INV-004, INV-011 | waiting queue items are parked until explicit wake; ambiguous provider resume is fail-closed | `src/dev_agent/scheduler/worker.py`, `src/dev_agent/runtime/controller.py` | `tests/v2/test_phase6_scheduler.py::test_worker_defers_waiting_task_until_explicit_wake`, `tests/v2/test_phase6_integration.py::test_paid_provider_timeout_waits_for_reconciliation_instead_of_failing` / G6O4 |
| INV-015 | workflow first | `workflows` | resolver Gate |
