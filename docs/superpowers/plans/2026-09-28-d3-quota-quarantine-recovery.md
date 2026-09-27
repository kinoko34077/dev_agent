# D3 Post-429 Quarantine Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a reset-expired quota probe to clear a 429 quarantine only for an exact trusted no-charge hard-stop resource when the probe reports no numeric quota headroom, while preserving the existing UNKNOWN/bootstrap-admission path and fail-closed behavior everywhere else.

**Architecture:** Extend the existing `QuotaRequalificationCoordinator` and `ResourceLedger` observation boundary only. A successful no-header probe stores a fresh observation with no fabricated limit/remaining values; the existing Router then admits it only when the caller explicitly allows unknown quota and the immutable trusted billing profile proves no-charge hard-stop. No new scheduler, quota authority, or retry loop is introduced.

**Tech Stack:** Python, SQLite ResourceLedger, existing quota coordinator/router, pytest.

**Spec:** `dev_agent` Issue #34, with D3 audit map in Issue #27 and admission boundaries in Issue #11/#26.

## Global Constraints

- Preserve `RUNTIME_ELIGIBLE=0` and formal `PHASE8_LIVE_ACTIVATION=NOT_VERIFIED`.
- Never fabricate limit, remaining, reset, quota ratio, billing, or paid/free facts.
- Only exact trusted no-charge/hard-stop resources may clear a post-reset quarantine without numeric headroom.
- Paid, quota-required, untrusted, authorization, permission, UNKNOWN/reconciliation, and failed-probe paths remain fail-closed.
- Use the existing maintenance/coordinator/router boundaries; do not add a Scheduler, StateStore, Provider registry, or Discord-specific path.
- Preserve fresh attempt, no-replay, quota-domain, health, circuit, billing, qualification, and authority semantics.

## Review Focus

- A successful HTTP/probe response without quota telemetry must not revive a paid or untrusted resource.
- A probe reporting numeric zero/invalid headroom must not be treated as a no-header success.
- The persisted recovery observation must contain no synthetic quota values and must route only through explicit unknown-quota admission.
- Existing positive-headroom requalification and external/authorization blocks must remain unchanged.
- Recovery must remain bounded to one probe and must not create a retry storm or formal eligibility.

---

### Task 1: Reproduce the D3 deadlock with focused failing tests

**Files:**
- Modify: `tests/v2/test_quota_scheduler.py`
- Modify: `tests/v2/test_phase6_router.py`

**Interfaces:**
- Consumes: existing `QuotaRequalificationCoordinator.probe_once`, `ResourceLedger`, and `ResourceRouter`.
- Produces: executable proof for trusted no-header recovery, non-trusted rejection, no fabricated quota values, and explicit unknown-quota routing.

- [x] **Step 1: Add a trusted exact-resource fixture**

Use the existing trusted catalog identity and persisted billing metadata, then create a reset-expired `rate_limit` observation with zero known headroom.

- [x] **Step 2: Add the failing trusted no-header recovery test**

Return only bounded probe metadata (`unit`/`metric`/`window`, with no limit/remaining pair), assert the current implementation rejects it, and pin the desired recovery result for the next step.

- [x] **Step 3: Add the routing authority test**

After recovery, assert ordinary routing remains `NoRoute`, while `allow_unknown_quota=True` selects the exact trusted resource and exposes no numeric quota values.

- [x] **Step 4: Add the fail-closed regressions**

Cover paid/untrusted no-header probes and a probe reporting numeric zero/invalid headroom; they must remain blocked and must not wake work.

- [x] **Step 5: Run only the new D3 tests and verify RED**

Run the focused test node IDs. Expected: the new trusted no-header test fails because the current coordinator returns `INVALID_OBSERVATION`; existing positive-headroom and external-block tests remain green.

### Task 2: Implement the narrow recovery contract

**Files:**
- Modify: `src/dev_agent/scheduler/quota.py`
- Modify: `src/dev_agent/resources/ledger.py` only if the existing ingestion boundary cannot persist an explicitly unblocked no-headroom observation without synthetic quota facts.

**Interfaces:**
- Consumes: a reset-expired block, one provider-owned probe result, exact resource metadata, and the immutable billing catalog.
- Produces: a fresh unblocked observation with `unknown_quota` evidence, `REQUALIFIED` only for the trusted path, and existing router-compatible persistence.

- [x] **Step 1: Add the exact trusted no-charge/hard-stop predicate**

Require trusted catalog authority, current exact profile, matching billing metadata, no-charge guarantee, and `hard_stop`; do not infer trust from `cost_minor=0` alone.

- [x] **Step 2: Distinguish no telemetry from reported non-positive telemetry**

Permit recovery only when the probe has no numeric quota fields and no reported block. Numeric zero, malformed, conflicting, or explicitly blocked payloads remain invalid/still blocked.

- [x] **Step 3: Persist a fresh unblocked unknown observation**

Record only bounded non-quota metadata and the observed timestamp. Do not copy stale limit/remaining values, invent a ratio/reset, or modify resource catalog/billing/qualification fields.

- [x] **Step 4: Project bounded evidence**

Expose whether the successful requalification is unknown-quota/bootstrap-admitted in the existing `QuotaProbeResult` projection without changing formal admission status.

- [x] **Step 5: Run the focused D3 tests and verify GREEN**

Run the new scheduler/router nodes plus existing quota scheduler and router nodes. Expected: trusted no-header recovery routes only with explicit unknown admission; all fail-closed cases remain blocked.

### Task 3: Record bounded evidence and reconcile the owning Issue

**Files/Surfaces:**
- `spec/v2/evidence/` only for bounded non-secret D3 evidence if implementation is accepted.
- Issue #34 and parent Issue #27 for result/handoff.

- [ ] **Step 1: Run the affected focused regression once**

Use the changed quota/router/provider boundary tests. Do not repeat unrelated Discord or full-suite tests unless a concrete failure or release rule requires it.

- [ ] **Step 2: Check protected Gate/admission invariants**

Confirm no change to `spec/v2/GATE_STATUS.json`, `RUNTIME_ELIGIBLE`, billing authority, credential state, or formal R9.

- [ ] **Step 3: Commit and push the coherent D3 slice**

Use a narrow commit and exact-head CI/review according to repository policy; do not merge or promote a Gate from local evidence alone.

- [ ] **Step 4: Update Issue #34/#27 with the reproduction, contract, tests, and exact CI**

Keep raw provider responses, credentials, and secrets out of Issues/evidence. Close the child only after formal review and exact-head checks are recorded.

## Self-review

- The plan changes only the existing quota observation/coordinator/router contract and its focused tests.
- Positive quota requalification remains the existing `REQUALIFIED` path; no new runtime scheduler is introduced.
- The no-header branch cannot be reached for paid/untrusted/blocked/invalid payloads and cannot create numeric quota evidence.
- Formal Gate and R9 remain outside this slice.
