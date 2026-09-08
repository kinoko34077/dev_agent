# Phase 6 Operational Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Phase 6 foundation into an operational control plane without falsely certifying unintegrated resource, provider, worker, or recovery behavior.

**Architecture:** Keep the v2 Kernel provider-neutral. Replace implicit float-to-money conversion with typed money and period-scoped reservations. Route every provider dispatch through a registry-backed dispatcher that records resource health and applies deterministic survival policy. Run queued Tasks only under a durable lease proof checked at every Controller transition and immediately before provider/tool dispatch. Keep Recovery independent of runtime imports and expose it through a narrow CLI.

**Tech Stack:** Python standard library, SQLite, dataclasses, `typing.Protocol`, pytest, existing v2 protocol/state/recovery modules.

**Spec:** `C:/Users/kinok/.codex/attachments/ce1d4e39-ece3-4802-8401-8cec8b0a5aee/pasted-text.txt`

## Global Constraints

- Phase 6 foundation and operational integration are distinct Gate states.
- Money is `currency + integer minor_units`; no implicit conversion from `ExecutionLimits.max_cost`.
- A paid dispatch requires a currency-bound worst-case price reservation for its explicit budget period.
- Missing actual charge leaves the reservation `unknown`; it is never reconciled to its estimate by default.
- Privacy constraints outrank cost/latency; stale observations and unavailable resources fail closed.
- The Controller contains no provider-specific conditionals and depends on typed protocols, not `Any`.
- Queue ownership is a lease proof; stale workers cannot commit state or start provider/tool dispatch.
- Maintenance mode rejects new worker claims and new runtime dispatches.
- Recovery mutation remains opt-in; Rescue does not import Controller, router, providers, or v1 runtime.
- No Phase 7 implementation, v1 deletion, ORM migration, framework adoption, or broad rewrite.

### Task 1: Correct Gate and Current-State Evidence

**Files:**
- Modify: `spec/v2/GATE_STATUS.json`
- Modify: `scripts/check_gate.py`
- Modify: `tests/v2/test_gate_checker.py`
- Modify: `docs/PHASE6_PLAN.md`

**Interfaces:** `phase6_foundation_status` is derived from F6 foundation records; `phase6_operational_status` is derived from operational records and cannot be manually marked VERIFIED.

- [ ] Add failing checker tests proving an unintegrated F6 item makes operational status `IN_PROGRESS`.
- [ ] Replace the manual Phase 6 VERIFIED claim with foundation verified / operational in progress evidence.
- [ ] Run `python -m pytest -q tests/v2/test_gate_checker.py` and `python scripts/check_gate.py`.

### Task 2: Money, Period, and Native Resource Reservations

**Files:**
- Modify: `src/dev_agent/resources/ledger.py`
- Modify: `src/dev_agent/resources/budget.py`
- Modify: `src/dev_agent/resources/control.py`
- Test: `tests/v2/test_phase6_resources.py`

**Interfaces:** `MoneyAmount(currency: str, minor_units: int)`, `BudgetPeriod(period_id, starts_at, ends_at)`, `ResourcePrice(currency, worst_case: MoneyAmount | None)`, and reservation records with period/unknown state.

- [ ] Write failing tests for cross-currency denial, period rollover isolation, unknown actual cost, absent worst-case price, independent-ledger reservation race, native request reservation/release/reconcile, and stale observation rejection.
- [ ] Implement ledger migrations and encapsulated transactions; BudgetGovernor must not access `ledger.connection` or `ledger._lock`.
- [ ] Run focused resource tests.

### Task 3: Provider Registry, Dispatcher, Health, and Survival

**Files:**
- Create: `src/dev_agent/providers/registry.py`
- Create: `src/dev_agent/providers/dispatcher.py`
- Modify: `src/dev_agent/resources/router.py`
- Modify: `src/dev_agent/resources/survival.py`
- Modify: `src/dev_agent/resources/control.py`
- Modify: `src/dev_agent/runtime/controller.py`
- Test: `tests/v2/test_phase6_dispatcher.py`

**Interfaces:** `ProviderRegistry`, `ProviderDispatcher.request(task_id, request)`, `DispatchPolicy` Protocol, `DispatchDenied(category, message)`, and `RouteRequest(task_class, mode)`.

- [ ] Write failing tests for actual selected-provider dispatch, primary circuit failover, typed health outcomes, survival paid-dispatch prohibition, and selected-provider audit.
- [ ] Implement registry/dispatcher routing and health updates; only transport/rate/quota failures count toward temporary circuit cooling.
- [ ] Run dispatcher and Controller regression tests.

### Task 4: Lease-Fenced Scheduler Worker and Maintenance Mode

**Files:**
- Modify: `src/dev_agent/scheduler/queue.py`
- Create: `src/dev_agent/scheduler/worker.py`
- Modify: `src/dev_agent/state/store.py`
- Modify: `src/dev_agent/state/sqlite_store.py`
- Modify: `src/dev_agent/runtime/controller.py`
- Modify: `src/dev_agent/tools/runtime.py`
- Test: `tests/v2/test_phase6_scheduler.py`
- Test: `tests/v2/test_phase6_worker.py`

**Interfaces:** `LeaseProof(task_id, worker_id, lease_token, state_version)`, `DurableQueue.claim()` returns proof, `LeaseFencedWorker.run_once()`, `StateStore.commit_transition(..., lease_proof=...)` and `ToolRuntime.execute(..., ownership=...)`.

- [ ] Write failing two-independent-connection/process claim tests, queue-to-Controller E2E, lease-renewal, stale proof commit denial, stale pre-dispatch denial, and maintenance claim denial.
- [ ] Implement same-database SQLite proof validation inside state/effect transactions and explicit maintenance mode.
- [ ] Run focused worker/scheduler tests plus integration hardening.

### Task 5: Recovery Operations and Rescue CLI

**Files:**
- Modify: `recovery/phase6_recovery.py`
- Create: `recovery/rescue.py`
- Modify: `recovery/backup.py`
- Modify: `recovery/diagnose.py`
- Test: `tests/v2/test_phase6_recovery.py`

**Interfaces:** `python -m recovery.rescue diagnose|validate-ledger|backup|restore-plan|rollback-plan|repair-branch-plan`; mutation needs explicit opt-in.

- [ ] Write failing tests for CLI read-only operations, backup/restore with artifact root manifest, maintenance exclusion, and disposable Git rollback/repair drills.
- [ ] Implement the narrow recovery-only command surface without Runtime/Provider imports.
- [ ] Run recovery tests and direct CLI smoke tests.

### Task 6: Targeted Refactors and Canonical Documentation

**Files:**
- Create: `src/dev_agent/security/audit.py`
- Modify: `src/dev_agent/runtime/controller.py`
- Modify: `src/dev_agent/tools/runtime.py`
- Modify: `src/dev_agent/state/store.py`
- Modify: `src/dev_agent/state/sqlite_store.py`
- Modify: `README.md`, `docs/V2_EXECUTION_PLAN.md`, `docs/PHASE6_PLAN.md`, `spec/v2/*`
- Test: focused existing and new tests

**Interfaces:** `AuditRecorder` owns event sanitization/record creation; `ToolRuntime.bound_to(store)` returns a new instance; `RuntimeState` wraps checkpoint JSON; targeted `has_event()` replaces runtime `snapshot()` scans.

- [ ] Write regression tests for immutable ToolRuntime bindings, typed runtime-state roundtrip, and targeted event lookup.
- [ ] Extract behavior without changing protocol payloads or legacy imports.
- [ ] Establish v2-first README and canonical-doc map; mark `dev_agent_codex_4docs` as reference only.

### Task 7: Operational Gate, Exact-HEAD Evidence, and Push

- [ ] Run full pytest, compileall, diff check, gate checker, recovery diagnostics, and direct Rescue CLI checks.
- [ ] Push each focused commit to `origin/v2/bootstrap`.
- [ ] Wait for exact-HEAD `v2-core` and `v2 tests` successes.
- [ ] Record only observed live/offline evidence and leave unperformed operator actions DEFERRED.
