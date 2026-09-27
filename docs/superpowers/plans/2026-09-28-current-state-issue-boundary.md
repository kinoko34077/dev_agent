# dev_agent Current State / Issue Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align `dev_agent`'s local agent entrypoint with the common devflow ownership rule so Current State is updated for accepted repository-level state changes rather than used as a running implementation chronology.

**Architecture:** Keep this as one narrow repository-local documentation change. `AGENTS.md` is the only normative local file to modify in this slice; existing historical `docs/CURRENT_STATE.md` cleanup remains separate work so the policy change can be reviewed and reverted independently.

**Tech Stack:** Markdown repository policy, GitHub Issue/PR, existing `tests/v2` and exact-head CI.

**Spec:** `kinoko34077/devflow/docs/superpowers/specs/2026-09-28-repository-doc-issue-ownership-boundary-design.md` and the accepted devflow policy PR that implements it.

## Global Constraints

- Base branch is `v2/bootstrap`; `main` remains the frozen v1 line.
- Do not touch `spec/v2/GATE_STATUS.json`, budget authority, recovery, credentials, `.env*`, `.git/` or `.devfarm/`.
- Do not change runtime, Provider, Router, quota, billing, qualification, Recovery or Gate behavior.
- Do not rewrite `docs/CURRENT_STATE.md` historical content in this slice.
- Task progress, implementation chronology and temporary blockers belong in the owning Issue / PR / Actions.
- Current State remains the owner of concise currently accepted repository-level technical state, current integration/environment facts and durable active limitations.
- When accepted state advances, stale Current State projections are replaced/retired rather than appended as a second chronological history.

## Review Focus

- The wording must not weaken the requirement to update Current State when repository-level accepted state actually changes.
- The wording must not move permanent requirements/specification into Issues only.
- The wording must distinguish task progress from durable current limitations.
- The change must not alter Phase 8 Gate semantics or D3/R9 execution authority.
- The change must not bundle historical cleanup or unrelated Phase 8 work.

---

### Task 1: Create/reuse the repository-local owning Issue

**Surface:**
- Repository-local Issue / Work Order on `kinoko34077/dev_agent`

- [ ] **Step 1: Re-read live devflow Control and repository state**

Confirm `v2/bootstrap` head and active work before mutation, and check for an existing Issue that already owns the Current State / Issue responsibility correction.

- [ ] **Step 2: Create/reuse a bounded docs Issue**

The Issue must reference the accepted common devflow ownership policy, define this slice as `AGENTS.md` wording only, and explicitly defer existing `CURRENT_STATE.md` history cleanup.

- [ ] **Step 3: Establish the manual Execution Session Record**

Record exact scope, base SHA, branch, exclusion of Phase 8 functional work, and next action before editing.

### Task 2: Correct the local Delivery wording

**Files:**
- Modify: `AGENTS.md`

**Interfaces:**
- Consumes: accepted common devflow ownership rule.
- Produces: unambiguous local delivery rule for future dev_agent agents.

- [ ] **Step 1: Re-read the exact live Delivery paragraph**

Confirm the current sentence still says `Update the owning Current State/traceability document when implementation evidence changes.` before replacing it.

- [ ] **Step 2: Replace only the ambiguous ownership wording**

The resulting guidance must state:
- update Current State when accepted repository-level current state changes;
- task progress, temporary blockers, commit chronology and verification runs stay in owning Issue / PR / Actions;
- reference evidence compactly from Current State where needed;
- replace stale projections rather than accumulating a running history;
- durable specification/design changes still update their owning canonical documents.

- [ ] **Step 3: Preserve all other Delivery constraints**

Keep focused tests, full `tests/v2` regression, read-only Gate checks, scoped commit/push, no history rewrite and no artifact-only Gate promotion unchanged.

- [ ] **Step 4: Commit the documentation change**

Commit only `AGENTS.md` plus this implementation plan if not already committed separately.

### Task 3: Verify and deliver the local policy alignment

**Files/Surfaces:**
- `AGENTS.md`
- owning repository Issue / Session Record
- PR targeting `v2/bootstrap`

- [ ] **Step 1: Run local repository regression**

Run:
`python -m pytest tests/v2 -q`

Expected: existing suite passes subject only to already-documented environment-bound exceptions; no new failure may be attributed to this docs-only change.

- [ ] **Step 2: Run compile/diff hygiene**

Run:
- `python -m compileall -q src scripts tests/v2`
- `git diff --check`

Expected: PASS.

- [ ] **Step 3: Run relevant read-only Gate/status checks**

Confirm the docs-only change does not alter protected Gate state or Phase 8 admission semantics.

- [ ] **Step 4: Open PR against `v2/bootstrap`**

PR must link the owning local Issue and accepted devflow policy, state that historical Current State cleanup is deferred, and record exact verification results.

- [ ] **Step 5: Formal Review and exact-head CI**

Use normal repository review/CI on the exact PR head. Any review finding that changes the common policy is routed back to devflow rather than silently redefining it locally.

- [ ] **Step 6: Merge after green exact-head evidence**

Use normal reversible merge; no protected Gate mutation or deployment is involved.

- [ ] **Step 7: Update local Issue and devflow Control only where owned state changed**

Close/release the local docs Issue when accepted. Update devflow Control only if its cross-repository summary/entrypoint state materially changes; do not copy the full implementation chronology there.

### Deferred follow-up: historical `CURRENT_STATE.md` cleanup

This plan does not edit historical Current State content. After the policy change is accepted, open a separate bounded cleanup Issue if the repository still contains appended historical checkpoint blocks that materially impede current-state readability. That cleanup must preserve durable current facts while relying on Git/closed Issues/PRs/Actions for historical implementation chronology.
