# Typed Durable Wait/Wake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put every non-terminal task wait on one typed, durable wait-condition contract with an explicit wake authority, predicate, deadline, and replay policy, without adding a scheduler or second state machine.

**Architecture:** Add a provider-neutral `WaitCondition` value object and a bounded registry in the domain layer. Existing Controller wait producers populate it while preserving legacy `wait_reason`/`wake_at` compatibility; WorkerRunner serializes the condition through the existing DurableQueue, and existing maintenance, quota, human, dependency, interruption, and reconciliation owners remain the only wake producers.

**Tech Stack:** Python 3.11, dataclasses/enums, existing SQLite StateStore, DurableQueue, Controller, WorkerRunner, pytest.

**Spec:** Repository Issue #70, `[WORK ORDER / Structural S0-C] Close every durable wait with a typed wake authority`.

## Global Constraints

- Reuse existing DurableQueue, checkpoint, lease/fencing, recovery, `WAITING_DEPENDENCY`, `wake_at`, and reconciliation boundaries.
- Do not add a Scheduler, timer thread, external polling loop, second wait state machine, or second state store.
- Preserve `UNKNOWN`/reconciliation no-replay semantics; reconciliation wake is only through confirmed reconciliation.
- Preserve legacy `wait_reason` strings and queue wake APIs for compatibility while making typed conditions the canonical projection.
- Keep Host Verification, Reviewer, Integration, security, approval, and Gate authority separate.
- No Provider calls, credential changes, paid fallback, Cloudflare replay, or Phase 8 Gate changes.

## Review Focus

- A persisted wait without a satisfiable predicate must fail closed — registry/validation tests.
- A provider-capacity wait must wake only the affected binding/pool — targeted queue wake test.
- A quota wait must retain its domain/deadline and use the existing quota authority — quota condition round-trip test.
- Reconciliation must never become an ordinary retry — no-replay policy test.
- Restart must preserve the condition and its wakeability — metadata/queue round-trip test.

### Task 1: Establish the typed wait-condition contract

**Files:**
- Create: `src/dev_agent/domain/wait.py`
- Modify: `src/dev_agent/domain/__init__.py` only if existing lazy exports require it
- Test: `tests/v2/test_wait_condition.py`

**Interfaces:**
- Produces `WaitKind`, `WaitReplayPolicy`, frozen `WaitCondition`, `WAIT_CONDITION_REGISTRY`, `condition_from_task_metadata(status, metadata)`, and `condition_from_reason(...)`.
- `WaitCondition` fields are bounded and serializable: `kind`, `subject`, `created_from`, `wake_authority`, `wake_predicate`, `deadline_epoch`, `replay_policy`, and a stable `queue_reason` projection.

- [ ] **Step 1: Write failing tests** for registry coverage of every deferred `TaskStatus`, typed mapping for user delay/provider capacity/resource/quota/maintenance/human/approval/dependency/reconciliation/budget, required predicate validation, and serialization round-trip.
- [ ] **Step 2: Run `python -m pytest tests/v2/test_wait_condition.py -q` and verify the new contract tests fail because the module/API is absent.
- [ ] **Step 3: Implement the minimal bounded enums, dataclass, registry, legacy-reason mapping, and strict validation.** Reconciliation must use a no-external-replay policy; unknown reasons must not silently become an untyped wait.
- [ ] **Step 4: Re-run the focused tests and verify they pass.
- [ ] **Step 5: Commit the contract with a narrow message.

### Task 2: Bind Controller wait producers to the contract

**Files:**
- Modify: `src/dev_agent/runtime/controller.py`
- Modify: `src/dev_agent/operation.py` only where queued/delayed waits are created or metadata is projected
- Test: `tests/v2/test_wait_condition.py`, `tests/v2/test_operation.py`, `tests/v2/test_operation_runtime.py`

**Interfaces:**
- Existing wait producers keep their public signatures and legacy metadata, and additionally persist `metadata["wait_condition"]` as the canonical serialized condition.

- [ ] **Step 1: Add failing assertions** that `_park_for_user_delay`, resource/no-route, provider saturation, maintenance, quota/budget, human/approval/dependency, and reconciliation waits persist a typed condition with the correct authority/predicate/replay policy.
- [ ] **Step 2: Run only the affected tests and confirm failures show missing typed metadata or incorrect mappings.
- [ ] **Step 3: Add one internal helper in the existing Controller/operation boundary to attach a condition while preserving `wait_reason`, `wait_until_epoch`, and existing event payloads.** Use binding/pool/domain subjects when already available; otherwise use bounded task/resource subjects and explicit manual/requalification authority.
- [ ] **Step 4: Re-run the affected tests; fix only regressions caused by the new metadata.
- [ ] **Step 5: Commit the producer integration.

### Task 3: Make WorkerRunner queue finalization typed and durable

**Files:**
- Modify: `src/dev_agent/scheduler/worker.py`
- Test: `tests/v2/test_wait_condition.py`, `tests/v2/test_phase6_scheduler.py`, `tests/v2/test_provider_saturation_and_wake.py`

**Interfaces:**
- Existing `DurableQueue.defer_until` and `defer_for_event` remain the persistence mechanisms. WorkerRunner consumes `WaitCondition` and chooses deadline vs event/manual deferral without changing queue schema.

- [ ] **Step 1: Add failing tests** for deadline waits, event waits, provider-saturation targeting, and fail-closed handling when a deferred status has no typed/persistable condition.
- [ ] **Step 2: Run the focused scheduler tests and verify the old untyped `queue.defer` path is exposed.
- [ ] **Step 3: Replace status/string inference in the deferred finalization branch with typed-condition projection, retaining compatibility derivation for legacy tasks and preserving reconciliation as event-only/no-replay.
- [ ] **Step 4: Re-run focused scheduler/provider wake tests and the existing operation wait tests.
- [ ] **Step 5: Commit the WorkerRunner integration.

### Task 4: Add lifecycle completeness and restart/recovery evidence

**Files:**
- Modify: `tests/v2/test_wait_condition.py` or add `tests/v2/test_durable_wait_authority.py` if separation improves clarity
- Modify: `src/dev_agent/runtime/controller.py` or `src/dev_agent/operation.py` only if a missing producer is found by the registry test
- Modify: `docs/CURRENT_STATE.md`

**Interfaces:**
- The registry is the testable source for supported wait families and their sole wake authority; existing wake methods remain the implementation owners.

- [ ] **Step 1: Add failing/diagnostic tests** for restart round-trip, no early user-delay wake, due wake, targeted provider wake, quota wake, human/approval wake, dependency wake, and reconciliation-only wake.
- [ ] **Step 2: Run the focused durable-wait suite and record any real failures as Issue #70 checkpoints.
- [ ] **Step 3: Fix only uncovered producer/authority gaps and update the registry completeness test so a new deferred status/reason cannot be added without an authority mapping.
- [ ] **Step 4: Run the focused suite once after the final fix; do not rerun unrelated green suites.
- [ ] **Step 5: Update `docs/CURRENT_STATE.md` once with the accepted #70 implementation/evidence baseline and next #71 handoff.
- [ ] **Step 6: Commit the docs/evidence synchronization.

### Task 5: Release verification and handoff

**Files:**
- No new production files; update owning Issue #70 and, if needed, devflow #15 through the live reporting path.

- [ ] **Step 1: Run focused affected tests, `python -m pytest tests/v2 -q`, Architecture, compileall, and secret/diff scans at the accepted slice because durable state/authority changed.
- [ ] **Step 2: Commit and push `v2/bootstrap`, then verify exact-head `v2-core` and `provider-smoke` for the exact final SHA.
- [ ] **Step 3: Add detailed Issue #70 checkpoints with implementation SHA, test/CI evidence, preserved no-replay boundaries, and next #71 action; close #70 only after all acceptance conditions are evidenced.
- [ ] **Step 4: Reassess the roadmap at the milestone: if #70 is complete, begin #71 shared lifecycle consolidation; otherwise keep the blocker concrete and do not advance to #72/#65.

