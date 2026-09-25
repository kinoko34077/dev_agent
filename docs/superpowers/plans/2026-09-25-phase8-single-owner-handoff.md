# Phase 8 single-owner Operation-to-DevFarm handoff

> **For the implementer:** REQUIRED SUB-SKILL: use `superpowers:executing-plans` to execute this plan task-by-task.

**Goal:** Make the Operation-to-DevFarm boundary durable and fail closed so one logical development child is owned by exactly one execution plane at a time.

**Architecture:** Reuse the existing Operation SQLite StateStore and DurableQueue. A DevFarm-owned planning proposal is persisted as `WAITING_DEPENDENCY` with a `HANDOFF_PENDING` marker and is never enqueued by Operation. A narrow Operation handoff method validates the complete proposal/child-key binding, records the DevFarm run/task identity durably, and leaves queue ownership empty. Dependency release remains Core-owned but must not enqueue DevFarm-owned children; later issue #7 will return explicit completion evidence through the existing dependency-release authority.

**Tech stack:** Python, dataclasses/typed mappings already used by `dev_agent`, pytest.

### Task 1: Add explicit execution-owner mode to planning

**Files:** `src/dev_agent/operation_planning.py`, `src/dev_agent/operation.py`, `tests/v2/test_planning_idempotency.py`

1. Add strict `execution_owner` validation with only `operation` and `devfarm` accepted.
2. Keep `operation` as the compatibility default and preserve its current queue behavior.
3. For `devfarm`, persist every independent child as `WAITING_DEPENDENCY` with bounded metadata marking `execution_owner=devfarm`, `handoff_state=HANDOFF_PENDING`, and `wait_reason=devfarm_handoff`; never enqueue it.
4. Ensure reapplication rejects an existing child whose owner does not match the requested owner.
5. Ensure planner dependency release never enqueues a DevFarm-owned child; dependency terminalization semantics remain unchanged.
6. Add focused tests for deferred queue ownership, owner mismatch, dependency-release non-enqueue, and default compatibility.

### Task 2: Add the durable handoff boundary

**Files:** `src/dev_agent/operation.py`, `tests/v2/test_phase8_production_composition_e2e.py`, `tests/v2/test_operation_runtime.py`

1. Add one public Operation method that accepts a proposal identity, bounded DevFarm run identity, and an exact child-key-to-DevFarm-task mapping.
2. Validate all bindings before mutation: unique keys/task IDs, exact persisted proposal children, matching `execution_owner`, `WAITING_DEPENDENCY`, no existing Operation queue item, and no conflicting prior handoff.
3. Atomically record `handoff_state=DEVFARM_OWNED`, `devfarm_run_id`, and `devfarm_task_id` through the existing StateStore transition event. Do not enqueue or dispatch from this method.
4. Make the same binding idempotent after restart; reject stale, partial, duplicate, unknown, or conflicting bindings without partial mutation.
5. Add tests proving Operation cannot claim the handed-off tasks, restart preserves ownership, and invalid/ambiguous handoffs fail closed.

### Task 3: Verify and report the bounded slice

1. Run the focused planning/composition/runtime tests with pytest cache disabled in the managed worktree.
2. Run focused compile and `git diff --check`.
3. Update issue #6 with the implementation and evidence; keep formal Phase 8 and production deployment gates unchanged.
4. Commit and push the verified slice to the existing PR branch; confirm exact-head CI.

**Out of scope:** new scheduler/queue/state store, DevFarm retry or approval authority, provider admission, Reviewer/integration return path (issue #7), Discord live E2E, and formal Phase 8 promotion.
