# Semantic Coherence Checks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the accepted canonical execution, wait/wake, provider normalization, and model-evidence semantics mechanically checkable before the next fresh R9 trial.

**Architecture:** Add one read-only diagnostic boundary under `scripts/` that composes existing domain/resource/provider contracts into a bounded machine-readable report. It must validate existing authorities rather than create a second router, lifecycle store, scheduler, provider registry, or Gate projection. The report is evidence only; `spec/v2/GATE_STATUS.json` and the owning Issues remain authoritative.

**Tech Stack:** Python standard library, existing `pytest` v2 tests, current `src/dev_agent/domain`, `src/dev_agent/resources`, and Provider adapters.

**Spec:** dev_agent Issue #73, AR-1/AR-2 in Issue #67, accepted #68/#69/#70/#71/#72 slices.

## Global Constraints

- No Provider/network calls, routing mutation, quota inference, Gate mutation, or UNKNOWN/reconciliation replay.
- Reuse `ModelRequest`/`ModelResponse`, `ExecutionRequirement`, `FieldPresence`, `WaitCondition`, `CanonicalExecutionBinding`, and `FunnelReport` as existing authorities.
- Provider-native request/response objects remain adapter-local; only normalized canonical values enter the check.
- Generated output is a bounded diagnostic projection and never replaces `GATE_STATUS.json` or manual Issue evidence.
- Direct Codex implementation is justified as `cross_cutting` and `no_qualified_worker`: the checks cross domain, resource, Provider, and development-script boundaries.

## Review Focus

- A newly added deferred task status without a typed wake authority must fail the report.
- `RUNTIME_BOOTSTRAP_ADMITTED` must never be counted as formal supply, and role-scoped supply must use exact route identity.
- `UNSPECIFIED`, `VALUE(T)`, and `EXPLICIT_NONE` must remain distinct; unsupported requested capability must fail closed.
- Operation and DevFarm projections for the same canonical facts must agree, while contradictory observations must fail rather than use precedence.
- Gemini and Cloudflare equivalent response envelopes must normalize to the same canonical semantics without exposing raw payloads.

---

### Task 1: Add the bounded semantic-coherence checker

**Files:**
- Create: `scripts/check_semantic_coherence.py`
- Test: `tests/v2/test_semantic_coherence_checks.py`

**Interfaces:**
- Consumes: `WAIT_CONDITION_REGISTRY`, `TaskStatus`, `FieldPresence`, `ExecutionRequirement`, lifecycle projection helpers, Gemini/Cloudflare decoder boundaries, and optional `FunnelReport`.
- Produces: `build_report() -> dict[str, object]`, `check_report(report) -> tuple[str, ...]`, and a JSON CLI with no network side effects.

- [x] **Step 1: Write RED tests** for wait coverage, funnel invariants, field presence, lifecycle projection/contradiction, adapter normalization, bounded report shape, and a failing synthetic mutation.
- [x] **Step 2: Run only the new test file** and confirm the import/API failure is caused by the missing checker.
- [x] **Step 3: Implement the smallest checker** with named PASS/FAIL checks and bounded details; do not duplicate routing or lifecycle logic.
- [x] **Step 4: Run the new test file plus the already-related canonical/funnel/lifecycle tests** and fix only regressions from this slice.
- [x] **Step 5: Run the checker CLI in read-only mode** and record the observed report in Issue #73 without treating it as Gate admission.

### Task 2: Integrate the checker into architecture/read-only verification

**Files:**
- Modify: `.github/workflows/v2-core.yml` only if the existing workflow has a narrow read-only diagnostic step for this class of check
- Modify: `tests/v2/test_architecture.py` or the closest existing architecture test only if required to prevent a second semantic authority
- Test: `tests/v2/test_semantic_coherence_checks.py`

**Interfaces:**
- Consumes: Task 1 report and current architecture checker.
- Produces: a focused CI-invokable coherence check; no new state or authority.

- [x] **Step 1: Inspect current CI/architecture entry points** and add no duplicate invocation if the existing architecture boundary already covers it.
- [x] **Step 2: Add only the minimum integration test** proving the checker remains read-only and bounded.
- [x] **Step 3: Run focused checks, architecture, compile, and secret scan once for this accepted slice.**

### Task 3: Synchronize durable evidence and release the slice

**Files:**
- Modify: `docs/CURRENT_STATE.md`
- Modify: Issue #73 via the Issue-first reporting boundary
- Modify: `docs/superpowers/plans/2026-10-04-semantic-coherence-checks.md` checkboxes as execution progresses

- [x] **Step 1: Record RED, implementation, focused GREEN, CLI report, and global judgment in #73.**
- [x] **Step 2: Update Current State once with the accepted implementation SHA, checks, remaining gaps, and the unchanged Gate.**
- [x] **Step 3: Commit and push the verified implementation slice.**
- [x] **Step 4: Verify exact-head CI for the implementation/evidence head.**
- [x] **Step 5: Decide whether #73 is accepted or needs another bounded slice; do not start #65 until all required coherence checks are covered and the Issue says so.**

### Task 4: Add bounded Provider decode-structure diagnostics

**Files:** `src/dev_agent/providers/base.py`, `src/dev_agent/providers/gemini/decoder.py`, `src/dev_agent/providers/cloudflare/provider.py`, `tests/v2/test_provider_decode_diagnostics.py`

**Approach:** Extend the existing `ProviderError` boundary with an optional,
bounded structural projection. The projection may contain transport metadata,
bounded top-level keys, value kinds, nested paths, a size bucket, a schema
fingerprint, and a decoder branch, but never raw response values or exception
text. Attach it only to provider-decode failures; transport, quota,
authentication, and reconciliation paths remain unchanged.

**TDD:** Add failing Gemini/Cloudflare malformed-envelope tests first; then
implement the projection helper and attach it at the existing decoder failure
boundaries. Verify that equivalent malformed shapes produce useful bounded
facts and that secret-shaped values are absent.

**Constraints:** Reuse `ProviderError`; do not add a provider registry, decode
store, retry path, or network call. Keep reconciliation semantics unchanged.

- [x] RED tests fail at collection before the projection exists.
- [x] Bounded diagnostics are attached only to decoder failures.
- [x] Affected provider/decode/coherence scope passes (`113 passed` before the
  state-projection slice; the decode/coherence subset passes after it).

### Task 5: Detect Current State / Gate projection drift

Add a read-only check to the same semantic-coherence report. It resolves the
current Git head and the two documented Current State baselines with a
command-local `safe.directory`, verifies that the documented heads are valid
ancestors, and compares the prose Gate line with `spec/v2/GATE_STATUS.json`.
The machine-readable Gate remains authoritative. A newer unrecorded working
head is reported as `sync_required` rather than being treated as a semantic
failure, so implementation can be committed before the single Current State
sync commit.

- [x] RED test covers the missing check and an unaccepted head marked for sync.
- [x] State projection check is included in the bounded CLI report.
- [x] Local focused test, Architecture, Compile, diff, and credential checks
  pass.

## Known follow-up gaps

This plan intentionally does not invent a second Current State generator, provider
diagnostic persistence store, or live admission route. The remaining release work
is durable Issue/Current State synchronization, exact-head CI for the final
implementation/evidence head, and the global decision whether every #73 acceptance
item is sufficiently covered before any R9 rerun.
