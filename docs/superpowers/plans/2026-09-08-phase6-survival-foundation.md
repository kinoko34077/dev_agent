# Phase 6A-6E Survival Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Phase 6A-6E resource, budget, routing, survival, recovery, scheduler, and verification boundaries without weakening the existing Kernel trust boundary.

**Architecture:** Add a small standard-library-only Phase 6 control-plane package. Resource and budget state is persisted in a dedicated SQLite ledger with integer minor units/Decimal-safe accounting, reservations are atomic and fail closed, the router consumes explicit capability/health/privacy/cost metadata, survival mode is derived by a deterministic governor, and scheduler claims use durable leases plus state versions. Recovery operations remain independent and explicitly gated. Phase 6E integrates the boundaries with the Controller-facing adapter, tests, Gate evidence, and current-state documentation.

**Tech Stack:** Python 3 standard library, SQLite, `Decimal`, dataclasses, pytest, existing v2 protocol and recovery modules.

**Spec:** `dev_agent_codex_4docs/02_dev_agent_foundation_requirements.md`, `dev_agent_codex_4docs/03_dev_agent_v2_current_roadmap_simple.md`, `dev_agent_codex_4docs/04_dev_agent_v2_current_roadmap_companion.md`.

## Global Constraints

- Resource types keep native units; tokens, requests, compute time, and money are never silently converted.
- Paid calls reserve budget before dispatch; unknown price fails closed; recovery reserve is isolated from normal spend.
- Privacy and permission constraints outrank cost and latency in routing.
- Survival mode is derived by deterministic policy, never selected by the model.
- Queue claims are durable and exclusive through `lease_owner`, `lease_until`, and `state_version`.
- Recovery defaults are read-only; rollback, branch creation, and restore publication require explicit opt-in.
- Existing Phase 3.5-5 behavior and v1 branch protections must remain intact.
- Every new production behavior gets a failing test before implementation.

### Task 1: Phase 6A Resource Ledger and Budget Governor

**Files:**
- Create: `src/dev_agent/resources/__init__.py`
- Create: `src/dev_agent/resources/ledger.py`
- Create: `src/dev_agent/resources/budget.py`
- Test: `tests/v2/test_phase6_resources.py`

**Interfaces:**
- `ResourceLedger(path).register_resource(...)`, `.observe(...)`, `.get_resource(...)`, `.list_resources()`
- `BudgetGovernor(ledger, policy).reserve(...)`, `.reconcile(...)`, `.snapshot(...)`
- `ResourceSpec`, `BudgetPolicy`, `BudgetReservation` are JSON-safe dataclasses.

- [x] Write failing tests for native-unit observations, atomic concurrent reservations, unknown-price rejection, hard cap rejection, and isolated recovery reserve.
- [x] Run `python -m pytest -q tests/v2/test_phase6_resources.py` and confirm the new interfaces fail for the expected missing-module reason.
- [x] Implement the SQLite ledger and integer minor-unit reservation accounting with explicit reservation states.
- [x] Run the focused tests and add restart persistence coverage.
- [x] Commit `feat: implement phase6 survival control plane`.

### Task 2: Phase 6B Router and Survival Modes

**Files:**
- Create: `src/dev_agent/resources/router.py`
- Create: `src/dev_agent/resources/survival.py`
- Test: `tests/v2/test_phase6_router.py`

**Interfaces:**
- `RouteRequest(capabilities, sensitivity, allowed_providers, max_cost, max_latency_ms)`
- `ResourceRouter.choose(request)` returns a provider/resource selection or a typed rejection.
- `SurvivalGovernor.evaluate(snapshot)` returns `NORMAL`, `CONSERVE`, or `SURVIVAL` with deterministic reasons.

- [x] Write failing tests for capability filtering, privacy precedence, unhealthy/cooldown exclusion, circuit breaker transitions, and deterministic mode thresholds.
- [x] Run the focused tests and confirm the expected failures.
- [x] Implement capability/health/quota/privacy routing, cooldown, and deterministic survival modes without model input.
- [x] Run router tests plus the Phase 6A tests.
- [x] Include routing and survival in `feat: implement phase6 survival control plane`.

### Task 3: Phase 6C Recovery Operationalization

**Files:**
- Create: `recovery/phase6_recovery.py`
- Modify: `recovery/diagnose.py`
- Modify: `recovery/backup.py`
- Test: `tests/v2/test_phase6_recovery.py`

**Interfaces:**
- `RecoveryOperator(root).snapshot()`, `.backup_state(...)`, `.restore_state(...)`, `.record_lkg(...)`, `.plan_rollback(...)`, `.create_repair_branch(...)`.
- All mutating operations require explicit keyword opt-ins and maintenance-lock ownership.

- [x] Write failing tests for independent diagnostics, backup/restore validation, maintenance-lock exclusion, LKG capture, rollback plan, repair-branch plan, and refusal of implicit mutation.
- [x] Run the focused tests and confirm missing operational wrapper behavior.
- [x] Implement the wrapper using existing recovery helpers; do not import Controller, Provider, or router modules.
- [x] Add diagnostics for Phase 6 ledger schema and persisted reservation integrity.
- [x] Run focused recovery tests and the existing recovery suite.
- [x] Include recovery controls in `feat: implement phase6 survival control plane`.

### Task 4: Phase 6D Durable Scheduler and Worker Ownership

**Files:**
- Create: `src/dev_agent/scheduler/__init__.py`
- Create: `src/dev_agent/scheduler/queue.py`
- Test: `tests/v2/test_phase6_scheduler.py`

**Interfaces:**
- `DurableQueue(path).enqueue(task_id, run_at, priority)`, `.claim(worker_id, now, lease_seconds)`, `.renew(...)`, `.complete(...)`, `.fail(...)`, `.reap_expired(...)`.
- `QueueItem` exposes `task_id`, `lease_owner`, `lease_until`, `state_version`, `attempts`.

- [x] Write failing tests for durable enqueue, exactly-one simultaneous claim, lease expiry/reclaim, stale-worker fencing, priority ordering, and restart recovery.
- [x] Run the focused tests and confirm the expected missing implementation failures.
- [x] Implement SQLite transactions using `BEGIN IMMEDIATE`, compare-and-set state versions, and explicit queue states.
- [x] Run scheduler tests under repeated claim contention and the full Phase 6 suite.
- [x] Include scheduler ownership in `feat: implement phase6 survival control plane`.

### Task 5: Phase 6E Kernel Integration, Gate and Documentation Sync

**Files:**
- Create: `tests/v2/test_phase6_integration.py`
- Modify: `src/dev_agent/runtime/controller.py`
- Modify: `spec/v2/GATE_STATUS.json`
- Modify: `spec/v2/TRACEABILITY.md`
- Modify: `spec/v2/INTEGRATION_HARDENING.md`
- Modify: `spec/v2/HARDENING_NEXT_PLAN.md`
- Modify: `dev_agent_codex_4docs/03_dev_agent_v2_current_roadmap_simple.md`
- Modify: `dev_agent_codex_4docs/04_dev_agent_v2_current_roadmap_companion.md`
- Modify: `docs/V2_EXECUTION_PLAN.md`
- Create: `docs/PHASE6_PLAN.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Controller receives an optional Phase 6 policy adapter; when present, every model/provider dispatch reserves and reconciles according to the adapter, while the default remains backward-compatible.
- Gate schema records separate 6A, 6B, 6C, 6D, and 6E evidence and an exact verification head.

- [x] Write failing integration tests for pre-dispatch budget denial, reservation reconciliation, survival-mode provider exclusion, queue-to-controller ownership, and independent recovery startup.
- [x] Run the focused integration tests and verify the expected missing adapter behavior.
- [x] Implement the smallest Controller adapter seam and preserve existing default behavior.
- [x] Update stale documents: current phase becomes Phase 6, Hardening plan becomes completed/frozen, Integration Hardening no longer says Phase 6 is still blocked, and the execution plan records current evidence.
- [x] Run full tests, compileall, diff check, Gate checker, and recovery diagnostics.
- [x] Commit and push `feat: implement phase6 survival control plane`; exact-head CI passed in runs 34231468796 and 34231468809.

## Verification Matrix

- Phase 6A: ledger restart, concurrent reservation, cap and reserve fail-closed tests.
- Phase 6B: capability/privacy/health routing and NORMAL/CONSERVE/SURVIVAL fault tests.
- Phase 6C: backup/restore/lock/LKG/rollback/repair diagnostics without Runtime imports.
- Phase 6D: two-worker claim race, lease expiry, stale fencing, and restart tests.
- Phase 6E: Controller budget boundary, exact-head CI, Gate checker, full suite, and synchronized current-state documents.
