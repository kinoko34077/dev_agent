# Lightweight Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with focused checkpoints.

**Goal:** Reduce repeated loading, broad imports, and mixed responsibilities while preserving v2 task lifecycle, budget/quota, authority, lease/fencing, privacy, fallback, escalation, and reconciliation behavior.

**Architecture:** Keep `SQLiteStateStore`, `ResourceLedger`, `OperationService`, `ProviderDispatcher`, and the existing intelligence pipeline as public facades. Extract only concrete change-reason boundaries: an indexed qualification catalog, a neutral lease-fencing primitive, Operation bootstrap/planning/control modules, and phase helpers. Pass short-lived views/context objects rather than introducing a new runtime, scheduler, database, or dependency-injection framework.

**Tech Stack:** Python 3.10/3.11, stdlib dataclasses/typing/AST, SQLite, pytest, existing Provider/Resource/State contracts.

**Spec:** `dev_agent 軽量化・効率化・依存整理リファクタリング指示書` supplied for `v2/bootstrap` at baseline `00596b8744403e30ad6d33d5a80b388f84208865`.

## Global Constraints

- Preserve external API and all durable lifecycle, budget, quota, approval, lease/fencing, privacy, UNKNOWN/reconciliation, fallback, escalation, and Gate semantics.
- Do not add a scheduler, state machine framework, ORM, async rewrite, Redis, or new runtime dependency.
- No import-time SQLite/JSON/credential/network side effects; qualification is loaded only during explicit Operation construction.
- Each behavior-preserving slice gets focused tests before implementation, then full `python -m pytest tests/v2 -q` before push.
- Keep `ResourceLedger` and `SQLiteStateStore` as the durable facades and keep DevFarm/Commander outside Production Runtime ownership.

---

### Task 1: Baseline and refactor evidence

**Files:**
- Create: `docs/superpowers/plans/2026-09-11-lightweight-refactor.md`
- Modify only if evidence requires: `docs/CURRENT_STATE.md`
- Test/measurement: `scripts/` measurement commands and existing test suites

**Interfaces:**
- Consumes: current `v2/bootstrap` checkout and existing docs.
- Produces: recorded baseline for HEAD, full regression, import/open timing, and qualification read behavior.

- [x] Step 1: Confirm branch, exact HEAD, clean status, and current Gate status with command-local `safe.directory`.
- [x] Step 2: Run `python -m pytest tests/v2 -q` and preserve the result as the pre-refactor baseline (`602 passed, 1 skipped`, 138.13s).
- [ ] Step 3: Measure `dev_agent` import time and representative `OperationService.open` time without changing production code.
- [x] Step 4: Record known external blockers without changing Gate status; G6O1 remains externally blocked.
- [x] Step 5: Commit only the plan/evidence documentation when no source change is needed (`2052d73`).

### Task 2: Indexed QualificationCatalog and canonical capability ownership

**Files:**
- Modify: `src/dev_agent/resources/qualification.py`
- Modify: `src/dev_agent/intelligence/capabilities.py` or create `src/dev_agent/domain/capabilities.py` only after import-direction inspection
- Modify: `src/dev_agent/resources/router.py`
- Modify: `src/dev_agent/operation.py` and the later bootstrap module
- Test: `tests/v2/test_capability_qualification_projection.py` and a focused catalog test

**Interfaces:**
- Produces `QualificationCatalog.load(path)`, an immutable indexed catalog keyed by `(provider_id, provider_binding_id, model_id)`, and `QualificationResolver(catalog=...)`.
- `QualificationResolver.resolve(...)` remains backward-compatible for existing callers, while Operation composition injects one resolver instance into tier resolution, resource projection, and `ResourceRouter`.

- [x] Step 1: Add failing tests for one explicit matrix load per catalog, exact-index lookup, duplicate identity rejection, invalid confidence rejection, expiry rejection, and shared resolver identity in Operation composition.
- [x] Step 2: Run only those tests and confirm they fail for the missing catalog/index/shared-instance behavior.
- [x] Step 3: Implement immutable catalog loading/validation/indexing without globals, import-time reads, refresh threads, or model-name tier inference.
- [x] Step 4: Move canonical routing vocabulary to one directionally neutral owner; keep task competencies/policy traits separate from provider execution capabilities.
- [x] Step 5: Inject the resolver through the existing Operation/Router composition and keep default construction only for compatibility/test callers.
- [x] Step 6: Run qualification, router, intelligence, and operation focused tests; full regression is `607 passed, 1 skipped`.

### Task 3: Hot-path context reuse

**Files:**
- Modify: `src/dev_agent/intelligence/planner.py` and its callers
- Modify: `src/dev_agent/resources/router.py` and dispatch/fallback callers
- Test: planner, task graph, routing, and operation tests

**Interfaces:**
- Produces a short-lived `PlanningContext` or equivalent containing one task snapshot, decoded tasks, graph, and parent/root indexes for one validation/apply operation.
- Keeps routing snapshots caller-scoped and refreshes them after health/quota/budget state changes.

- [x] Step 1: Add a failing regression test counting one state snapshot per planning apply; routing already had caller-scoped snapshot reuse and an existing regression for refresh-by-caller behavior.
- [x] Step 2: Run the focused test and verify the current repeated-read behavior was observable (`2` snapshots).
- [x] Step 3: Implement planning context reuse without changing TaskGraph limits, dependency semantics, or durable state ownership.
- [x] Step 4: Run planner focused tests; routing snapshot behavior remains covered by `tests/v2/test_phase6_router.py`.

### Task 4: Lease primitive and narrow State views

**Files:**
- Create: `src/dev_agent/persistence/__init__.py`
- Create: `src/dev_agent/persistence/lease.py`
- Modify: `src/dev_agent/state/sqlite_store.py`
- Modify: `src/dev_agent/scheduler/queue.py` and lease callers
- Modify: narrow protocol definitions near the existing State contracts
- Test: state/scheduler lease, recovery, effect-intent, and architecture tests

**Interfaces:**
- `persistence.lease` owns `LeaseProof`, `StaleLease`, and `assert_active_lease(connection, proof)`.
- `SQLiteStateStore` remains transaction owner; component-facing Protocol views expose only the operations each component needs.

- [x] Step 1: Add an import regression test showing State no longer imports concrete Scheduler modules while lease fencing remains atomic.
- [x] Step 2: Run the test and confirm the current reverse import is detected.
- [x] Step 3: Move only the shared lease proof/check into the neutral persistence module; preserve the SQL transaction and stale-lease behavior.
- [x] Step 4: Add/use narrow State Protocol views without splitting the SQLite connection or changing public facade methods.
- [x] Step 5: Run state, scheduler, recovery, and architecture focused tests; focused result is `44 passed`.
- [x] Step 6: Run the full regression; result is `611 passed, 1 skipped`.

### Task 5: Operation composition decomposition

**Files:**
- Create: `src/dev_agent/operation_bootstrap.py`
- Create: `src/dev_agent/operation_planning.py`
- Create: `src/dev_agent/cli.py` if CLI extraction is safe after import inspection
- Create/modify: `src/dev_agent/state/control_repository.py` only if it can reuse the existing SQLite connection/SSOT
- Modify: `src/dev_agent/operation.py`
- Test: `tests/v2/test_operation.py`, planner/CLI/stop tests

**Interfaces:**
- `OperationService` retains `open`, `start`, `submit`, `status`, and `stop` behavior.
- Bootstrap owns configuration/provider/qualification/resource/budget composition.
- Planning module owns proposal validation, child creation, and dependency release.
- Control repository exposes only stop control operations and does not create a second StateStore.

- [x] Step 1: Existing Operation tests cover existing-resource non-overwrite, stop persistence, maintenance, planner, and reviewed escalation; the new boundary test fixes the intended ownership.
- [x] Step 2: Run the characterization tests before moving code; Operation boundary plus Operation suite result is `33 passed`.
- [x] Step 3: Move the durable `OperationControl` repository into `state/control_repository.py` and preserve the `dev_agent.operation.OperationControl` compatibility export.
- [x] Step 4: Add a dedicated `cli.py` boundary with the existing parser and command behavior unchanged; keep lazy compatibility wrappers in `operation.py`.
- [x] Step 5: Extract bootstrap construction into `operation_bootstrap.open_components` with unchanged callback compatibility and cleanup semantics.
- [x] Step 6: Extract planning context, proposal validation/application, and dependency release into `operation_planning.py`; keep facade methods as compatibility wrappers.
- [x] Step 7: Verify `python -m src.dev_agent --help` and the focused Operation suite after the CLI extraction.
- [x] Step 8: Run the bootstrap/Operation/planner focused suite; result is `44 passed`.

### Task 6: Import graph reduction and cold paths

**Files:**
- Modify: `src/dev_agent/providers/__init__.py`, `src/dev_agent/intelligence/__init__.py`, and other barrel modules only as required
- Modify: `src/dev_agent/providers/factory.py`
- Modify: legacy provider import sites and migration documentation
- Test: import/export smoke tests and AST architecture tests

**Interfaces:**
- Public compatibility exports remain available; internal modules use leaf imports.
- ProviderFactory imports only the adapter needed for the requested ProviderDefinition.

- [x] Step 1: Add failing subprocess import checks for unnecessary barrel and eager adapter imports.
- [x] Step 2: Run them and confirm the pre-change provider and intelligence barrels loaded the full adapter/pipeline set.
- [x] Step 3: Implement lazy compatibility exports and ProviderFactory local construction imports; public exports remain stable.
- [x] Step 4: Leave legacy provider compatibility paths intact; no active path was removed while the package imports became cold.
- [x] Step 5: Run import/provider/intelligence suites; results are `25 passed`, `19 passed`, and `10 passed` across the focused slices.

### Task 7: Controller and Ledger readability slices

**Files:**
- Modify: `src/dev_agent/runtime/controller.py`
- Modify only where justified: `src/dev_agent/resources/ledger.py`, add `resources/schema.py` or `resources/quota_store.py`
- Test: controller regression suite and ResourceLedger public-contract tests

**Interfaces:**
- Controller keeps one iterative loop and existing durable state transitions; private helpers return normalized outcomes.
- ResourceLedger remains the only public facade; callers do not access private stores.

- [ ] Step 1: Add characterization tests for success, ToolCall, resume, timeout, quota, budget, saturation, late completion, cancel, UNKNOWN, and reconciliation.
- [ ] Step 2: Run those tests before extraction.
- [ ] Step 3: Extract semantic private phase handlers without introducing a state-machine/handler framework.
- [ ] Step 4: Move Ledger schema/quota internals only when tests show a clear boundary; retain transaction semantics.
- [ ] Step 5: Run the controller/resource focused suites and commit separate `refactor:` slices.

### Task 8: Architecture/preflight tooling and final validation

**Files:**
- Create: `scripts/check_architecture.py`
- Create: `scripts/test_scope.py` and a simple affected-test map
- Modify: CI workflow only if current check names remain compatible
- Modify: `docs/CURRENT_STATE.md`, `docs/SYSTEM_MAP.md`, `spec/v2/05_api_spec.md`, `spec/v2/06_implementation_spec.md`, `spec/v2/TRACEABILITY.md`, `spec/v2/MIGRATION_MATRIX.md`
- Test: architecture/preflight tests

**Interfaces:**
- Preflight checks compile/import/architecture/static schema without becoming a full regression substitute.
- Affected-test mapping accelerates feedback but final acceptance remains the full `tests/v2` suite and exact-head CI.

- [ ] Step 1: Add failing AST checks for forbidden dependency directions and internal barrel imports.
- [ ] Step 2: Implement the stdlib-only checks with a small explicit allowlist.
- [ ] Step 3: Add affected-test scope data and a command that prints the focused paths for changed files.
- [ ] Step 4: Measure import time, Operation open time, qualification load count, planner snapshot count, focused time, and full-suite time before/after.
- [ ] Step 5: Run `python -m pytest tests/v2 -q`, read-only Gate checks, and exact-head CI.
- [ ] Step 6: Synchronize docs without changing Gate status; commit `test: add architecture and affected-scope checks` and the final documentation slice.

## Stop Conditions

- Stop a slice if a focused regression changes behavior rather than only structure; revert the structural part and isolate a bug fix.
- Do not advance to the next stage until the current stage has focused tests, full regression when required, and a clean standalone commit.
- Do not claim performance improvement without before/after measurements.
- Do not start Codex App Server, MCP, new scheduler/state machine, or external framework work in this refactor batch.
