# Risk-Bounded Autonomous Development — First Operational Slice Implementation Plan

**Status (2026-10-22):** The bounded runbook slice in this plan is complete via
PR #98 (merged as `f3bf5283d89bb0c0e27a0e551f9a2d03e3ba28c1`) and Current State
reconciliation via PR #99 (merged as `3a366785b67649612817b80347522aa62db61bef`).
The current branch tip `52b71036b84695e2db8cee254dfbeea73cda32a6` has exact-head
CI recorded as passing in Issue #97. This documents only the bounded runbook
slice: Worker proposal, Claim/Lease, Host Verification of a Worker patch, and
the complete autonomous runtime cycle remain **NOT DEMONSTRATED**. The later
Host-child local-trial admission gap is a separate continuation tracked in
Issue #97 and is not completed by this plan. Formal #65/#11 status is unchanged.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exercise a real, low-risk development cycle using the approved two-scope design, reusing existing dev_agent mechanisms. Do not add a second runtime. Keep formal Phase 8 blocked unless its existing evidence requirements independently pass.

**Architecture:** Bounded Operations and Formal Phase 8 are policy/acceptance scopes over the existing Commander, DevFarm, Supervisor, Operation/RuntimeCoordinator, Provider admission, Host Verification, and integration boundaries. Shared execution coordination is used only for work whose current contract requires its Claim.

**Tech Stack:** Python 3.10/3.11; existing `scripts/devfarm.py`, `scripts/devfarm_commander.py`, `scripts/devfarm_supervisor.py`; `pytest`; GitHub Issues/Actions; existing local/offline routes only when their current authorization and cost boundary is verified.

**Spec:** `docs/superpowers/specs/2026-10-21-risk-bounded-autonomous-development-design.md`. The user approved both the two-scope design and this bounded implementation plan on 2026-10-21. That approval does not extend to Gate changes, paid Providers, credentials, permissions, deployment, or other separately gated actions.

**Global Constraints:**

- Maintain `v2/bootstrap`; never use frozen `main` as the v2 implementation baseline.
- Do not change `spec/v2/GATE_STATUS.json`, #11 authority, #65 acceptance, billing/qualification, credentials, sessions, permissions, release/deployment, OS startup, or public scope.
- Do not replay UNKNOWN/reconciliation-required effects or use a paid/potentially billable Provider.
- No new scheduler, queue, lifecycle, Claim authority, StateStore, approval store, or Agent framework.
- Keep the approved untracked design file; do not stage unrelated user changes.
- Revalidate all current sources before execution. Devflow bootstrap/work-view/Inbox failures mean coverage is unknown, not empty; use live Control/owner Issues/#503 fallback and report the outage accurately.
- Do not manufacture a task or Claim. If no current Claim-governed candidate is evidenced, leave Claim-to-integration proof `NOT DEMONSTRATED` and continue only independent authorized work.
- No background service/schedule or assumption that this Codex turn remains alive.

## Review Focus — Risks and Required Checks

1. **Formal acceptance accidentally inferred from local success.** Task 5 verifies `spec/v2/GATE_STATUS.json` remains unchanged and runs `tests/v2/test_gate_checker.py` if any code/Gate-adjacent path is touched.
2. **Inbox visibility or an empty Claim snapshot mistaken for authority.** Task 1 records source, scope, expected revision, and exact Claim evidence; it never infers claimability from the Inbox. The required full Claim-to-integration proof remains `NOT DEMONSTRATED` without a valid live Claim/ack for a genuinely Claim-governed candidate.
3. **Paid route or UNKNOWN replay introduced during a fallback.** Task 3 verifies the chosen execution path is local/offline or exact current no-charge admission; any provider-path change must pass `tests/v2/test_provider_failover_semantics.py` and `tests/v2/test_phase6_provider_reconciliation.py` before use.
4. **A second scheduler/state/authority is introduced.** Task 5 runs `scripts/check_architecture.py` and `tests/v2/test_architecture_script.py` if code or architecture documentation changes imply a new mechanism.
5. **Existing Commander ownership or user changes are overwritten.** Tasks 1–4 inspect active plan ownership and preserve the untracked approved spec; `tests/v2/test_devfarm_plan_ownership.py` and `tests/v2/test_devfarm_plan_state_boundary.py` are required if Commander plan code changes.

## File Map

- `docs/superpowers/specs/2026-10-21-risk-bounded-autonomous-development-design.md` — approved design artifact; include it in the scoped documentation operation without changing its decisions.
- `docs/CODEX_DAILY_DOGFOOD.md` — add the concise operator procedure for the two scopes, risk-based stopping, and objective-level replan, reusing the current Commander/Supervisor steps.
- `.devfarm/plan-input.json` and `.devfarm/plans/<fresh-run-id>.json` — created only after plan approval and a live overlap check, through the existing Commander contract; contain no credentials or raw conversation.
- `docs/CURRENT_STATE.md` — reconcile the stale top-level HEAD/implementation/evidence projection only after accepted work is integrated; keep formal Gate facts unchanged.
- `spec/v2/GATE_STATUS.json` — read-only invariant for this slice; must remain byte-for-byte unchanged.
- `scripts/devfarm.py`, `scripts/devfarm_commander.py`, `scripts/devfarm_supervisor.py` — existing operation interfaces to reuse; no code edits planned unless the operational slice demonstrates a concrete defect and a separately reviewed narrow plan authorizes it.
- `tests/v2/test_devfarm_commander.py`, `tests/v2/test_devfarm_supervisor.py`, `tests/v2/test_devfarm_integration_boundary.py`, `tests/v2/test_devfarm_plan_ownership.py`, `tests/v2/test_architecture_script.py`, `tests/v2/test_gate_checker.py` — existing verification surfaces; run only the checks required by the actual diff, plus release-boundary evidence.

## Tasks

### Task 1: Refresh live authority and identify the first real work item

**Files:**
- Read: `AGENTS.md`, `docs/CURRENT_STATE.md`, `docs/SYSTEM_MAP.md`, `docs/V2_EXECUTION_PLAN.md`, `docs/V2_DETAILED_ROADMAP.md`, `spec/v2/GATE_STATUS.json`
- Read live records: devflow #734/#15/#503; dev_agent #2/#11/#40/#64/#65/#81; execution-coordinator #3/#107; GitHub branch comparison and current Actions/PR state
- Modify: none

**Interfaces:**
- Consumes: live Control/Issue/Inbox records and exact repository revision
- Produces: a bounded candidate record with source, scope, expected revision, acceptance, exclusions, recovery path, and Claim applicability; or an explicit `NOT DEMONSTRATED` result

- [x] **Step 1: Refresh live records read-only.** Devflow bootstrap/work-view are available, but work-view coverage is `INCOMPLETE`; explicit Codex Inbox retrieval failed. Manual Control #15, exact owner Issues, and KAEP #503 were checked as fallback.
- [x] **Step 2: Reconfirm local and remote baseline.** `v2/bootstrap@332a497277c452873e364af6024e6c9c1185c1e5` matches GitHub; exact-head v2-core/provider-smoke succeeded. New isolated branch `codex/bounded-autonomy-issue-97` is based on that SHA. Preserve the approved local design/plan files.
- [x] **Step 3: Select only a real authorized candidate.** The candidate is this directly Human-authorized, repository-local, reversible runbook improvement. It does not require an execution-coordinator Claim; the full Claim-governed acceptance remains `NOT DEMONSTRATED`. No Worker route was selected.
- [x] **Step 4: Record the source checkpoint.** Created owning dev_agent Issue #97. Its current blocker and precise limits are recorded; no TASK/Claim was inferred from incomplete views.

**Verification:** read-only comparison of source Issue, Control, current coordinator #3, and repository revision. No code test applies. Do not alter machine-maintained Control snapshots.

**Commit:** none; this is a read-only preflight.

### Task 2: Establish the owning work record and existing Commander plan

**Files:**
- Create only if no valid owner exists: one GitHub Issue for the approved bounded slice
- Create via approved existing plan boundary: `.devfarm/plan-input.json` and `.devfarm/plans/<fresh-run-id>.json`
- Use: `python scripts/devfarm.py plan <spec> --root .`; `python scripts/devfarm_supervisor.py status <run-id> --root .`

**Interfaces:**
- Consumes: Task 1 candidate record and `CommanderPlanStore` active ownership
- Produces: one schema-valid Commander Plan with fresh run ID, exact base revision, one bounded task, non-overlapping allowed paths, acceptance, and explicit delegation reason

- [x] **Step 1: Resolve the owner.** No current Issue owned this two-lane operational slice; created dev_agent #97. No synthetic TASK was created.
- [x] **Step 2: Check plan ownership.** Inspected unfinished Commander plans. The stale D7 plan was reviewed and superseded through the existing Supervisor; no remaining active path ownership conflicts with `docs/CODEX_DAILY_DOGFOOD.md`.
- [x] **Step 3: Create/validate the Commander Plan.** Commander plan `risk-bounded-autonomy-issue97-20261021` was created and completed through the existing Supervisor path; the bounded runbook task was integrated through PR #98.
- [x] **Step 4: Confirm Claim scope.** This initial runbook edit is repository-local work and is not automatically a shared execution-coordinator Claim task. Do not claim that this satisfies full cross-repository operational-cycle acceptance.

**Verification:** if plan-contract code changes, first add a failing case to `tests/v2/test_devfarm_commander.py` or `tests/v2/test_devfarm_plan_ownership.py`, run it to confirm failure, implement the minimal change, then run `python -m pytest tests/v2/test_devfarm_commander.py tests/v2/test_devfarm_plan_ownership.py -q`. With no code change, validate the produced plan with the existing CLI and status projection. Confirm no path overlap, credential, or invented Claim.

**Commit:** no source commit yet; the Commander plan is durable development state under the existing ignored `.devfarm` path.

### Task 3: Align the daily operating procedure to the approved two-scope design

**Paths:** `docs/CODEX_DAILY_DOGFOOD.md`; `docs/superpowers/specs/2026-10-21-risk-bounded-autonomous-development-design.md` (retain as approved; no decision changes).

- [x] **Step 1: Compare the current runbook to the approved design.** Material gaps were recorded and addressed in PR #98.
- [x] **Step 2: Update the runbook and retain the design artifact.** The Bounded Operations procedure was integrated through PR #98 without changing the approved design.
- [x] **Step 3: Keep existing authority language intact.** PR #98 preserves no-paid-provider, admission, UNKNOWN/no-replay, Host Verification, Review, Integration, and #65/#11 boundaries.
- [x] **Step 4: Verify the rendered Markdown contract.** Manual wording/link review and `git diff --check` passed; exact-head CI passed on the PR and integrated revisions.

**TDD / verification:** This is documentation-only, so do not add a brittle test that asserts prose. Before editing, capture the missing runbook behavior as the failing manual checklist; after editing, verify each requirement against the approved design, inspect Markdown links, run `git diff --check`, and confirm the Gate file is unchanged. No Python test is needed unless implementation code changes.

**Implementation route:** Prefer an existing currently available, exact, zero-charge local Worker only if its route is verified at execution time. Otherwise keep the docs task Codex-owned with `delegation_reason=no_qualified_worker` or `delegation_overhead`; never switch to paid remote work. Record the actual route and ownership in the Commander Plan.

**Commit:** do not hand-edit an official commit. Integrate only after Task 4’s existing Host verification and review path.

### Task 4: Run the bounded operation through existing Host verification and integration

**Paths / interfaces:** existing Commander manifest and proposal artifacts; `python scripts/devfarm_supervisor.py run/review/integrate <run-id> ...`; `docs/CODEX_DAILY_DOGFOOD.md`.

- [x] **Step 1: Execute the existing Supervisor path.** The existing Commander/Supervisor route completed the bounded runbook plan.
- [x] **Step 2: Host-verify the proposal.** The scoped proposal passed the existing verification/review path; no secret or Gate change was included.
- [x] **Step 3: Review and integrate.** PR #98 was reviewed and merged through the normal repository path. This does not claim a Worker-generated patch.
- [x] **Step 4: Handle local-route absence truthfully.** The docs work remained Codex-owned with a concrete delegation reason; Worker/model execution remains not demonstrated.

**Tests:** for the documentation-only slice, run `git diff --check` and the focused Commander/Supervisor boundary tests only if their code or plan semantics changed. Run `tests/v2/test_devfarm_integration_boundary.py` if the Host integration path is exercised and its exact contract is part of acceptance. Do not run the full suite merely because the docs changed.

**Commit:** use the existing Supervisor integration helper; no manual `git commit`, force push, or history rewrite.

### Task 5: Acceptance evidence, Current State reconciliation, and global replan

**Paths:** owner Issue from Task 2; `docs/CURRENT_STATE.md`; `spec/v2/GATE_STATUS.json` (read-only); devflow #15 only if the cross-repository active-work projection genuinely changes.

- [x] **Step 1: Persist bounded evidence.** Issue #97 records the exact integration revisions, route/delegation limits, verification, review, CI, and blockers; no secrets or raw provider output were added.
- [x] **Step 2: Reconcile Current State.** PR #99 reconciled the projection; the current docs-only tip is recorded separately from the implementation baseline in Issue #97.
- [x] **Step 3: Assert the formal boundary.** Gate authority and #11 were not changed; #65 remains `PRECHECK_BLOCKED` without new accepted evidence.
- [x] **Step 4: Publish and verify the exact revision.** The non-rewritten `v2/bootstrap` tip `52b71036b84695e2db8cee254dfbeea73cda32a6` is pushed and its exact-head CI is recorded passing in Issue #97.
- [x] **Step 5: Re-evaluate the larger objective.** Issue #97 records that no Worker/Claim/runtime cycle is proven and identifies the separate Host-child local-trial boundary as the next bounded question; no R9 or Formal Gate advancement was inferred.

**Tests / acceptance:** documentation-only: `git diff --check` plus Markdown link review. If source changes occur, run only changed-scope focused tests first; at the final accepted revision run the repository-required exact-head checks and architecture/compile checks required by the diff or branch rules. Verify the Gate file hash is unchanged. Do not mechanically repeat a passing test without a new failure or relevant diff.

**Commit:** final state is the deterministic integration commit; push only after review and required checks. Do not merge into legacy `main`.

## Plan Completion Boundary

This plan completes only the first Bounded Operations procedure/update and its real existing-path execution evidence. It does not claim a Claim-governed cross-repository cycle unless a valid live candidate/Claim was observed; does not complete Formal Phase 8; does not enable paid Providers, services, schedules, or deployment; and does not assert unattended autonomy. The broader approved goal continues after this slice; any code defect beyond this documentation/operational slice gets a narrow follow-up plan before implementation.
