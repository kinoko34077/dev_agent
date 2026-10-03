# Plan: Issue #50 Conservative Free-Tier Admission

**Goal:** Implement the repository-side portion of Issue #50 without changing
the protected Phase 8 Gate, credentials, billing settings, or live provider
state. A no-charge replenishing route must be able to expose a durable,
explicitly derived-conservative quota state that survives restart and can be
hard-stopped on provider exhaustion.

**Scope boundary:** The Cloudflare C1 model-identity mismatch remains a
separate evidence task. This plan does not select a similar model by inference
or perform live provider probes. It adds the local contracts and fixtures
needed once a fresh dispatchable identity is supplied.

**Existing boundaries to reuse:** `ResourceLedger`, its SQLite schema and
migrations, `ResourceControlPlane`/`ProviderDispatcher` quota observation
hooks, `RuntimeAdmissionEvaluator`, Cloudflare's existing model-specific
Neuron conversion, and the existing no-charge billing metadata.

**Verification policy:** Use focused tests for each changed boundary. Skip
unchanged broad regression layers unless a focused test exposes an impact or
the slice reaches commit/CI acceptance. Exact-head CI remains required for the
accepted commit.

## Tasks

### 1. Durable period ledger in the existing ResourceLedger

Files/interfaces:

- `src/dev_agent/resources/schema.py`
- `src/dev_agent/resources/free_tier.py` (small SQLite store, not a second DB)
- `src/dev_agent/resources/ledger.py`
- `tests/v2/test_free_tier_quota_ledger.py`

TDD steps:

1. Add failing tests for daily period identity, conservative debit,
   restart persistence, reset-at-boundary, and provider-exhaustion hard stop.
2. Add a schema migration and store keyed by provider/binding/model/domain and
   allowance period. Persist allowance ceiling, consumed value, reset boundary,
   authority/evidence mode, and blocked-until state.
3. Expose narrow `ResourceLedger` methods for read, debit, reset-if-due, and
   mark-exhausted operations. Reject paid/unknown billing metadata at this
   boundary; do not infer no-charge from cost alone.
4. Run only the new focused ledger tests plus migration compatibility tests.

### 2. Cloudflare conservative Neuron accounting contract

Files/interfaces:

- `src/dev_agent/providers/cloudflare/provider.py`
- `src/dev_agent/resources/free_tier.py` or a provider-specific pure helper
- `tests/v2/test_live_provider_adapters.py`
- `tests/v2/test_free_tier_quota_ledger.py`

TDD steps:

1. Add failing tests for exact model identity, ceil rounding, zero usage,
   missing/ambiguous usage, and the `derived_conservative` evidence label.
2. Reuse the existing model-specific rate table, make the conversion contract
   explicit, and emit bounded daily allowance/reset metadata for the exact
   selected identity only.
3. Keep provider-reported usage authoritative when supplied; never fabricate a
   provider-reported remaining value.

### 3. Admission and dispatch integration

Files/interfaces:

- `src/dev_agent/resources/runtime_admission.py`
- `src/dev_agent/resources/router.py` or the existing admission helper
- `src/dev_agent/resources/control.py`
- `src/dev_agent/providers/cloudflare/provider.py`
- focused admission/dispatch tests

TDD steps:

1. Add failing tests proving derived-conservative is distinct from
   `RUNTIME_BOOTSTRAP_ADMITTED`, that only an independently trusted no-charge
   route can use it, and that paid/unknown billing remains ineligible.
2. Allow a route to become formally eligible only when the durable ledger is
   active, the exact identity/qualification/health gates pass, and the route's
   hard-stop behavior is configured. Keep the protected Gate unchanged.
3. Debit after bounded provider usage is known; if usage cannot be bounded,
   stop or apply an explicitly conservative debit. A Cloudflare exhaustion
   response parks the route for the current period and does not trigger paid
   fallback or retry storms.
4. Add restart, exhaustion, no-paid-fallback, and missing-usage regressions.

### 4. Evidence and issue synchronization

Files/interfaces:

- Issue #50 and related #40/#42/#11 comments
- `docs/CURRENT_STATE.md` only after the accepted implementation slice is
  verified; preserve historical chronology and protected Gate state

Steps:

1. Record the implementation SHA, focused evidence, and the remaining C1
   external evidence boundary in Issue #50.
2. Reconcile #42/#40/#11 only after implementation and exact-head CI pass.
3. Do not report formal `RUNTIME_ELIGIBLE` or Phase 8 activation until the
   exact fresh Cloudflare identity and full R9 chain are independently proven.
