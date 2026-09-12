# Supervisor Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing development-only Commander supervisor run until a real intervention boundary, recover stale dispatches without duplicate external effects, and preserve compact review/rework/integration evidence.

**Architecture:** Extend the existing Commander plan JSON and DevFarm artifacts in place. `CodexSupervisedCommanderRun` remains a thin facade over `refresh_plan`, `dispatch_plan`, `collect_plan`, `verify_plan`, `reassign_task`, and `mark_integrated`; no scheduler, database, or parallel state machine is introduced. Host-side deterministic operations perform orphan classification, patch normalization/validation, review persistence, and Git integration only after explicit approval.

**Tech Stack:** Python stdlib, existing JSON Commander plans, existing `.devfarm` artifacts, SQLite metrics, pytest, Git CLI.

**Spec:** User-provided Supervisor / low-token wait / external payload reference requirements and the current `docs/CODEX_SUPERVISOR.md` boundary.

## Global Constraints

- Preserve `Task`, `Scheduler`, `Budget`, `Authority`, `Host Verification`, `UNKNOWN`, and reconciliation semantics.
- Do not add a production scheduler, new state machine, new database, LLM adapter, Compression Service, MCP, or automatic approval.
- External Worker output is never authority; validate actual artifacts on the Host.
- `STATIC_ONLY` never executes external generated code; `TRUSTED_HOST_EXEC` remains attempt-scoped and explicitly approved.
- Unknown external outcome is not blindly retried.
- Every source change has a failing focused test before implementation and a focused regression after implementation.

---

### Task 1: Durable dispatch ownership and run-until-intervention

**Files:**
- Modify: `scripts/devfarm_commander.py` — persist dispatch identity/timestamps, recover stale `DISPATCHED` tasks, export recovery helper.
- Modify: `scripts/devfarm_supervisor.py` — add bounded `run_until_intervention()` and `run` CLI, invoke orphan recovery before resume.
- Modify: `scripts/devfarm_supervisor_protocol.py` — add compact dispatch/recovery metadata validation and wake kinds.
- Test: `tests/v2/test_devfarm_supervisor.py`, `tests/v2/test_devfarm_commander.py`.

**Interfaces:**
- `dispatch_plan()` records `dispatch_id`, `dispatch_started_at`, `dispatch_deadline_at`, and `dispatch_owner_pid` before calling the existing orchestrator.
- `recover_orphaned_dispatches(root, run_id, now=None)` returns the plan with only expired/dead-owner `DISPATCHED` tasks classified as `BLOCKED` with `block_reason=orphaned_dispatch` and `dispatch_recovery=reconciliation_required`.
- `CodexSupervisedCommanderRun.run_until_intervention(..., max_wait_seconds, sleep_fn, monotonic_fn)` repeats only non-intervention passes and sleeps at the stored 1/5/10/15-minute cadence; normal synchronous proposal execution remains unchanged.

- [ ] Write tests for dispatch metadata, dead-owner recovery, deadline recovery, no recovery before deadline, and no blind retry.
- [ ] Run the focused tests and observe the expected failures.
- [ ] Implement the smallest durable metadata/recovery path using existing plan CAS.
- [ ] Add `run` CLI with an explicit bounded wait/deadline.
- [ ] Run focused Supervisor/Commander tests.
- [ ] Commit `feat: add bounded supervisor run recovery`.

### Task 2: ReviewPacket and durable ReviewDecision

**Files:**
- Modify: `scripts/devfarm_supervisor_protocol.py` — bounded review packet/decision normalization.
- Modify: `scripts/devfarm_supervisor.py` — build compact packets and record review requests/decisions.
- Modify: `scripts/devfarm_commander.py` — preserve top-level `review_decisions` in Plan schema.
- Test: `tests/v2/test_devfarm_supervisor.py`, `tests/v2/test_devfarm_commander.py`.

**Interfaces:**
- `ReviewPacket` contains task/attempt identity, result/verification artifact references, changed files, patch digest, acceptance summary, and bounded known issues; it never contains patch/stdout/stderr/raw conversation.
- `ReviewDecision` allows only `APPROVE_INTEGRATION`, `REWORK`, `REJECT`, or `ESCALATE`, with findings/evidence references and optional correction.
- Supervisor metadata stores bounded `review_packets`; the Plan stores append-only `review_decisions`.
- `codex_review_request_count` increments when a review wake is created; `codex_review_count` increments only when a decision is recorded.

- [ ] Add round-trip and raw-payload rejection tests.
- [ ] Run focused tests to confirm missing packet/decision behavior.
- [ ] Implement bounded normalization and persistence through Commander CAS.
- [ ] Return packets from `SupervisorStep.to_dict()` without inline patch contents.
- [ ] Run focused tests.
- [ ] Commit `feat: persist supervisor review evidence`.

### Task 3: Rework Handoff to revised attempt

**Files:**
- Modify: `src/dev_agent/handoff/presets.py` — reviewer-origin authority and explicit rework semantics.
- Modify: `scripts/devfarm_supervisor.py` — attach a compact rework handoff reference to a new attempt manifest.
- Modify: `scripts/devfarm_commander.py` — preserve manifest history and rework reference without overwriting prior artifacts.
- Test: `tests/v2/test_handoff_references.py`, `tests/v2/test_devfarm_supervisor.py`, `tests/v2/test_devfarm_commander.py`.

**Interfaces:**
- `rework_request()` uses `authority_source=review_decision` (not `current_user_instruction`) and keeps Human precedence above model review.
- `reassign(..., rework_handoff=...)` creates a new manifest path with the same base/scope and only the compact correction/reference delta; the old manifest remains immutable and is listed in `manifest_history`.

- [ ] Add a regression proving reviewer correction is not serialized as a Human instruction.
- [ ] Add a regression proving reassign creates a new manifest and leaves the old attempt/reference intact.
- [ ] Run focused tests and observe failures.
- [ ] Implement revised manifest persistence through existing validation.
- [ ] Run focused tests.
- [ ] Commit `fix: connect supervisor rework to next attempt`.

### Task 4: Explicit deterministic Git integration helper

**Files:**
- Modify: `scripts/devfarm_supervisor.py` — add `integrate_approved_worker()` that applies the verified patch in the intended checkout, commits it, then calls existing `mark_integrated()`.
- Modify: `scripts/devfarm_commander.py` only if a small public helper is required; do not alter proof checks.
- Test: `tests/v2/test_devfarm_supervisor.py`, `tests/v2/test_devfarm_commander.py`.

**Interfaces:**
- `integrate_approved_worker(task_id, decision_reference, commit_message, target_ref)` requires `HOST_VERIFIED`, a durable `APPROVE_INTEGRATION` decision, the verification artifact digest, and an explicit target checkout.
- The helper uses Git `apply --check`, `apply`, `add` for the verified changed paths, and `commit`; it never pushes, merges, changes protected paths, or marks integration without Git proof.

- [ ] Add tests for missing approval, digest mismatch, apply failure, and successful commit plus `mark_integrated`.
- [ ] Run focused tests and observe failures.
- [ ] Implement deterministic integration and cleanup only task-scoped temporary state.
- [ ] Run focused tests.
- [ ] Commit `feat: add approved supervisor integration helper`.

### Task 5: Independent parallel proposal failures

**Files:**
- Modify: `scripts/devfarm_orchestrator.py` — collect each future result/exception independently.
- Modify: `scripts/devfarm_commander.py` — preserve successful sibling results and classify only failed tasks.
- Test: `tests/v2/test_devfarm_orchestrator.py`, `tests/v2/test_devfarm_commander.py`.

**Interfaces:**
- One provider exception becomes that task's failed result; another task's valid proposal remains collectable.
- No exception path changes approval, scope, budget, or verification policy.

- [ ] Add a two-assignment test with one exception and one valid proposal.
- [ ] Run focused tests and observe failure.
- [ ] Implement per-future result normalization.
- [ ] Run focused tests.
- [ ] Commit `fix: isolate parallel worker proposal failures`.

### Task 6: Deterministic Worker patch transport hardening

**Files:**
- Modify: `scripts/devfarm.py` or `scripts/devfarm_worker.py` — safely normalize only unified-diff hunk counts, preserving paths/content and recording the normalization.
- Test: `tests/v2/test_devfarm_patch_validation.py`, `tests/v2/test_devfarm.py`.

**Interfaces:**
- A syntactically parseable patch whose hunk counts disagree with its literal lines may be normalized by Host-side deterministic counting; path, scope, protected-path, whitespace, and content validation still run afterward.
- Malformed headers, unsafe paths, binary patches, shell content, and mismatched `changed_files` remain rejected.

- [ ] Add tests for one-line hunk-count correction and rejection of malformed/unsafe patches.
- [ ] Run focused tests and observe failure.
- [ ] Implement normalization as a transport-only transformation with metrics/evidence.
- [ ] Run focused patch validation tests.
- [ ] Commit `fix: normalize bounded worker patch transport`.

### Task 7: End-to-end supervisor evidence and documentation

**Files:**
- Modify: `tests/v2/test_devfarm_supervisor.py` — full fake Worker run, review, rework, integration, and restart coverage.
- Modify: `docs/CODEX_SUPERVISOR.md`, `docs/CODEX_COMMANDER.md`, `docs/DEVFARM.md`, `docs/CURRENT_STATE.md`, `docs/V2_EXECUTION_PLAN.md`.
- Modify: `spec/v2/05_api_spec.md`, `spec/v2/06_implementation_spec.md`, `spec/v2/TRACEABILITY.md`.
- Modify: `scripts/test_scope.py` if new test clusters are needed.

- [ ] Add fake end-to-end coverage for READY → dispatch → result → verification → review packet → explicit approval → Git integration.
- [ ] Add restart coverage for stale `DISPATCHED` and no duplicate dispatch.
- [ ] Run focused Supervisor/DevFarm tests.
- [ ] Run `python scripts/check_architecture.py` and `python -m compileall -q src recovery scripts`.
- [ ] Run `python -m pytest tests/v2 -q`.
- [ ] Update docs with exact evidence, explicit non-goals, and external Worker evidence boundaries.
- [ ] Check exact-head GitHub Actions and commit/push the verified slice.

## Verification Gate

The slice is not complete unless the fresh output shows:

- focused Supervisor/Commander/DevFarm tests pass;
- `ARCHITECTURE_PASS`;
- compileall succeeds;
- full `tests/v2` regression passes;
- worktree is clean after commit;
- exact-head CI is checked for the pushed revision;
- external Worker attempts are reported as proposal/verification evidence only, never as successful integration without Host and Git proof.
