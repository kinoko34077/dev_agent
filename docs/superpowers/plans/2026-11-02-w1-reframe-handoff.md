# W1 Feasibility Reframe Handoff Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Give the upper planner a bounded, exact-manifest reference and safe constraints when a Worker pairing is blocked by deterministic feasibility.

**Architecture:** Extend the existing read-only Supervisor feasibility projection. Persist the canonical manifest digest with the pre-dispatch feasibility result, then bind a reference-first reframe request to the blocked task, current attempt, immutable result, and matching validated manifest. The request remains proposal-only; existing PlanningBridge validation is still required before any candidate is accepted, and this slice does not write Commander state or dispatch a Worker.

**Tech Stack:** Python, existing Worker/Commander/Supervisor, `load_worker_manifest`, `canonical_digest`, pytest.

**Spec:** dev_agent Issue #146, W1; `docs/DEVFARM.md`; `scripts/devfarm_supervisor.py`; `scripts/devfarm_planning_bridge.py`.

## Global Constraints

- Reuse the existing Supervisor and PlanningBridge; add no scheduler, queue, store, provider route, or retry framework.
- Keep the failed attempt immutable and never retry it as the reframe.
- Do not persist a candidate, write `.devfarm`, or dispatch a Provider in this slice.
- Keep `invalid_acceptance` distinct from `reframe_required`.
- Expose no raw Worker response or arbitrary result prose.

## Review Focus

- Missing, stale, malformed, or ownership-mismatched source manifests produce no usable reframe request.
- Only exact current `reframe_required` evidence yields a request; `invalid_acceptance` never does.
- The request preserves base revision, allowed ownership, acceptance, and independent verification requirements.
- Candidate work remains unpersisted and undispatched until the existing Host boundary validates it.

---

### Task 1: Pin the reframe handoff contract

**Files:** `tests/v2/test_devfarm_supervisor.py`, `tests/v2/test_devfarm_worker_preflight.py`

- [x] Add a test for an exact blocked attempt with a valid manifest: request includes source attempt/result/manifest reference and canonical digest, bounded constraints, and no raw Worker prose.
- [x] Add a test that missing or mismatched manifest identity yields an unavailable handoff.
- [x] Add a test that a pre-dispatch feasibility result records the exact normalized manifest digest used for assessment.
- [x] Add a test that `invalid_acceptance` receives no reframe request.
- [x] Run only these new tests and confirm they fail for the missing handoff behavior.

### Task 2: Implement the read-only handoff

**Files:** `scripts/devfarm_worker.py`, `scripts/devfarm_supervisor.py`

- [x] Use the existing validated manifest loader and canonical JSON digest.
- [x] Record the digest on the immutable pre-dispatch result; old artifacts without it must not receive a reframe handoff.
- [x] Add the handoff only for a current `reframe_required` result whose task, attempt, and result reference already pass the existing identity checks.
- [x] Return references and fixed constraints only; never copy free-text Worker output or mutate state.
- [x] Run the focused Supervisor tests and existing malformed-evidence regressions.

### Task 3: Close and hand off

**Files:** `docs/CURRENT_STATE.md`, Issue #146

- [ ] Update only the current projection and Issue checkpoint; keep W1 partial until a smaller candidate passes existing PlanningBridge/Host validation.
- [x] Run `git diff --check`.
- [x] Resolve review finding: require canonical safe attempt IDs before constructing immutable result paths; add a regression proving a matching nested result cannot produce a handoff.
- [ ] Re-run independent review and exact-head CI on the corrected PR head before merge.

**Checkpoint:** the exact-source handoff is now available in the read-only `SupervisorStep.worker_feasibility` projection. It does not itself create a candidate or mutate `.devfarm`; W1 remains partial pending a smaller candidate that passes the existing PlanningBridge/Host boundary. Initial local verification: focused reframe tests 7 passed; Supervisor/CLI/Worker modules 63 passed; full `tests/v2` 2270 passed, 1 skipped; architecture and compileall passed. Independent review then found that `result_reference()` accepted slash-bearing attempt IDs; a new regression reproduced a nested-artifact handoff before the fix, and the shared reference builder now uses the existing safe artifact-ID validator. Current focused verification: 57 passed across plan queries, integration boundary, and Supervisor tests. Corrected-head review/CI and post-merge Current State synchronization remain pending.
