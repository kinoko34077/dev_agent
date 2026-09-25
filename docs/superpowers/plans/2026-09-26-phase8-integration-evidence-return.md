# Phase 8 DevFarm integration evidence return

> **For the implementer:** REQUIRED SUB-SKILL: use `superpowers:executing-plans` to execute this plan task-by-task.

**Goal:** Return only authoritative, deterministic DevFarm integration evidence to the mapped Operation child and release the existing Operation-owned continuation through the existing dependency predicates.

**Architecture:** Keep independent implementation children explicitly DevFarm-owned after the existing handoff. Keep dependency-bearing continuation children Operation-owned and parked in `WAITING_DEPENDENCY`. Add one narrow Operation evidence boundary that joins `planning_proposal_id + planner_child_key + devfarm_run_id + devfarm_task_id`, requires Host Verification and approved review evidence, persists bounded integration metadata/event, and invokes the existing `release_planner_dependencies()` implementation. No new dependency graph, scheduler, queue, approval authority, or integration path.

**Tech stack:** Python, existing Operation SQLite StateStore/DurableQueue, existing DevFarm integration contract, pytest.

### Task 1: Keep ownership split explicit

1. In planner application, independent children requested for DevFarm become `HANDOFF_PENDING`; dependency-bearing continuation children remain Operation-owned `WAITING_DEPENDENCY`.
2. Restrict the DevFarm handoff binding to exactly the independent DevFarm-owned child set.
3. Preserve default Operation-owned planning behavior and existing dependency release tests.

### Task 2: Return bounded integration evidence

1. Add a narrow `OperationService.record_devfarm_integration_evidence()` boundary.
2. Require exact mapped identity, `INTEGRATED`, Host Verification `PASS`, `APPROVE_INTEGRATION`, fixed evidence source, bounded revision/attempt/digest, and no Operation queue ownership.
3. Persist only minimum integration metadata plus a durable evidence event.
4. Invoke existing `release_planner_dependencies()`; do not reimplement dependency rules.
5. Make identical evidence replay idempotent and conflicting/non-integrated/ambiguous evidence fail closed.

### Task 3: Verify

1. Run focused Phase 8/planning/runtime tests and the affected Operation regression with pytest cache disabled in the managed worktree.
2. Run focused compile and `git diff --check`.
3. Update issue #7 and push the slice to the existing PR branch; inspect exact-head CI.

**Out of scope:** reviewer policy, provider admission/retries, Discord live E2E, new scheduler/queue/state store, formal Gate promotion, and production deployment.
