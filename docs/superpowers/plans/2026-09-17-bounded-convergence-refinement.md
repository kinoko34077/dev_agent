# Bounded Convergence Refinement Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

## Goal

Connect the existing Planner Critic, Worker refinement, ReviewPacket,
Host Verification, DurableQueue, OperationService, and RuntimeCoordinator
behind one bounded convergence contract. A successful first attempt must stay
on the fast path with zero extra model calls. A failed attempt must produce a
sanitized, deterministic failure fingerprint and at most one Host-selected
next action per fresh attempt. No new scheduler, state store, retry engine, or
agent framework is introduced.

The implementation proceeds in small, independently verifiable slices. The
first code slice is R0: a reusable metadata/fingerprint value layer that can
be attached to existing refinement and planning artifacts without owning
dispatch, persistence, approval, integration, or external-effect replay.

## Architecture

Existing authority remains unchanged:

`Provider/Planner/Worker -> bounded artifact -> deterministic Host validation ->
existing Refinement/Reviewer/Integration boundaries`.

The new convergence value layer is pure and Host-owned. It records attempt
identity, normalized failure evidence, validation progress, and finite stop
reasons. It never calls a provider, changes a task, chooses a route, grants
approval, writes Git, or decides that an UNKNOWN external effect is safe to
replay.

R0 metadata is carried as an optional field on existing refinement/planning
artifacts where a compatible extension is possible. Durable storage continues
to be the existing plan/attempt/evidence store. Planner and Worker adapters
remain separate; they consume the same fingerprint and convergence rules but
do not communicate directly.

## Tech Stack

- Python 3.10 and 3.11
- frozen dataclasses and enums for bounded contracts
- existing `ensure_json_safe`, `ensure_secret_free`, identifier/text bounds
- pytest under `tests/v2`
- existing architecture check, compileall, full v2 regression, and exact-head CI
- existing Commander/Supervisor/Operation/Provider/Review boundaries

## Global Constraints

- Work on `v2/bootstrap`; preserve unrelated user changes and existing plans.
- Use one current checkout and do not create a parallel production scheduler,
  StateStore, retry framework, or Agent framework.
- Keep Work Address/UUID, ownership, lease/fencing, Egress, approval,
  Recovery, UNKNOWN/reconciliation, and Host integration semantics intact.
- Fast-path success performs no Critic call and no extra model call.
- Each correction is a fresh attempt with a new identity; never overwrite a
  failed attempt or replay an UNKNOWN external effect.
- Exclude raw provider responses, raw exception messages, credentials, secrets,
  personal data, and unrestricted free text from fingerprints/evidence.
- Only normal repository source and tests are eligible for routine Worker work;
  convergence authority, recovery, security, budget, and integration remain
  Codex/Host-owned. If a Worker-eligible slice cannot be safely dispatched,
  record `no_qualified_worker` rather than bypassing the boundary.
- Production Deployment, Task Scheduler/Windows Service, paid providers,
  GitHub bypass redesign, and authority-state directory/ACL migration remain
  deferred.

## R0 — Convergence Contract and Failure Fingerprint

Files:

- Add `src/dev_agent/intelligence/convergence.py` as the pure bounded value
  layer.
- Extend `src/dev_agent/intelligence/refinement.py` only where an optional
  `ConvergenceMetadata` field can be round-tripped without changing policy
  ownership.
- Add `tests/v2/test_convergence.py` and focused compatibility cases to
  `tests/v2/test_adaptive_refinement.py` and planner tests as needed.

Contract:

- Define bounded states for fast path, refinement, progress, no-progress,
  stuck, completed, and non-converging/terminal stop.
- Define a normalized `FailureFingerprint` from only failure class,
  validator/test references, response-contract type, patch/application
  category, and bounded error code. Canonicalize ordering and deduplicate
  values before hashing with SHA-256.
- Define immutable `ConvergenceMetadata` containing:
  `refinement_round`, `source_attempt_id`, `fresh_attempt_id`,
  `failure_class`, `failure_signature`, `correction_actor`,
  `previous_model_identity`, `current_model_identity`, `validator_refs`,
  `convergence_state`, and `stop_reason`.
- Validate UUID/identifier fields, round bounds, collection bounds, enum
  values, JSON safety, and secret-free content. Do not accept raw failure
  prose as a fingerprint input.
- Provide deterministic `to_dict`/`from_dict` round trips and a helper that
  tells Host whether two normalized observations have the same signature.
- Preserve optional backward compatibility: artifacts without convergence
  metadata remain valid and successful existing plans remain unchanged.

Test-first steps:

- [x] Add failing tests for canonical ordering/deduplication and stable digest.
- [x] Add failing tests proving raw exception/secret text is rejected or never
  included in the digest.
- [x] Add failing tests for metadata round-trip, fresh-attempt identity, bounds,
  invalid state, and same-vs-different signature comparison.
- [x] Implement the smallest pure contract and run the focused tests.
- [x] Add the optional existing-artifact integration and compatibility tests.

## R1 — Fast Path and Refinement Path

Files:

- Extend the existing refinement/planner composition modules only as needed.
- Add focused tests under `tests/v2` for call-count and path selection.

Rules:

- [x] Represent a successful deterministic validation as a terminal fast-path
  convergence record with `refinement_round=0`, no correction actor, and no
  extra call.
- [x] Represent a failed validation as a refinement record that requires a
  fresh attempt identity and exactly one Host-selected action.
- [x] Prove that a successful Planner skips Planning Critic and a successful
  Worker skips L1 Critic/Reviewer refinement calls until its existing review
  stage.
- [x] Prove that provider, security, authority, and UNKNOWN outcomes never
  enter model refinement.

## R2 — Validation Ladder

Reuse existing validators and Host Verification; do not build a second test
runner.

- [x] Add bounded validation-rung values V0 through V6 and a compact
  observation/result projection that references existing artifacts/tests.
- [x] Add deterministic ordering/short-circuit rules: V0 failure stops later
  rungs; correction restarts at V0; V4/V5 remain independent Host/Reviewer
  boundaries; V6 remains post-integration CI evidence.
- [x] Add tests for first-failure selection, correction restart, and no full-CI
  invocation after a V0 failure.
- [x] Connect the projection to existing attempt/refinement artifacts without
  moving authority into the projection.

## R3 — Planner Convergence

Reuse `ModelPlanningAdapter`, `ModelPlanningCriticAdapter`, and
`RootPlanningValidator`.

- [x] Add a Host-owned bounded planner convergence composition for only
  `invalid_json`/`invalid_proposal` responses already observed from a fresh
  provider request.
- [x] Enforce max four correction rounds, same signature at most twice in a
  row, fresh Critic identity/session per round, and no raw conversation carryover.
- [x] Attach R0 metadata to each failed and fresh corrected attempt while
  retaining immutable failure artifacts.
- [x] Stop with a bounded `NON_CONVERGING` projection after diversity/escalation
  limits; do not automatically alter quota, billing, privacy, or UNKNOWN
  semantics.
- [x] Test fast-path zero calls, one corrected proposal, repeated signature
  stop, changed-signature progress, transport exclusion, and identity reuse
  rejection.

Implementation note: `416ce96` connects this R3 composition to the existing
Planner Shadow CLI. The CLI records `FAST_PATH` for a valid Planner without a
Critic request, and uses one independently admitted proposal-only Critic for a
response-contract failure before returning to Host validation. Evidence is
`spec/v2/evidence/planner-shadow-bounded-convergence-20260917.json`.

## R4 — Worker Convergence / AR1 Composition

Reuse `scripts/devfarm_refinement.py`, existing `RefinementContext`,
`BoundedRefinementPolicy`, `rework_handoff`, and `reassign`.

- [x] Attach R0 metadata to existing Worker failure/refinement projections.
- [x] Ensure FORMAT/PATCH uses deterministic correction first where available,
  otherwise one Critic/Worker fresh attempt; SEMANTIC/TEST uses bounded Critic
  findings and fresh Worker rework.
- [x] Preserve one-axis model/reasoning changes, bounded attempts, and zero
  Codex direct implementation for Worker-owned work.
- [x] Add focused tests proving immutable failed attempts, fresh identities,
  no Critic on success, no refinement for external/security/UNKNOWN failures,
  and correct fingerprint reuse detection.

## R5 — Reviewer Feedback Loop

- [x] Project independent Reviewer `REWORK` findings into the existing
  reference-first refinement packet.
- [x] Create a fresh Worker attempt and return through Host Verification before
  reviewer re-evaluation.
- [x] Prove Reviewer remains proposal-only: no Git mutation, integration,
  approval consumption, ownership, or Gate promotion.
- [x] Add a bounded regression for APPROVE fast path and REWORK one-action path.

## R6 — Convergence Ratchet and Finite Stop

- [x] Add a pure comparator for failure count, validation rung, signature
  resolution, and changed-failure progress.
- [x] Emit `PROGRESS`, `NO_PROGRESS`, `STUCK`, and `NON_CONVERGING` using the
  configured finite budget: four rounds, same signature twice, one diversity
  change, one tier escalation, one final Codex diagnostic proposal.
- [x] Keep tier/reasoning escalation Host-selected and one-axis only.
- [x] Test that no unbounded loop is introduced and that stop reasons survive
  serialization/restart through existing durable artifacts.

## R7 — External Failure Non-Blocking Runtime

Reuse `OperationService`, `DurableQueue`, maintenance, dependency wake,
reconciliation, and `RuntimeCoordinator`.

- [x] Add only the missing convergence projection needed to distinguish a
  refinement stop from `WAITING_RECONCILIATION`.
- [x] Test that one UNKNOWN/external task remains durable while unrelated READY
  work continues, and that only durable reconciliation wakes the original task.
- [x] Prove no UNKNOWN request is replayed and no external failure is counted
  as model convergence failure.

## R8 — Durable Phase 8 Root

- [x] Connect the existing Phase 8 fresh-root composition to durable task/attempt
  metadata and RuntimeCoordinator entry points, without creating another
  scheduler or state machine.
- [x] Preserve Planner → Implementer A/B → Host Verification → Reviewer → Host
  integration → `CODE_INTEGRATED` continuation and all ownership/lease checks.
- [x] Add a headless/local regression that survives coordinator restart without
  duplicate task or attempt dispatch.

## R9 — Zero-Human Live Trial and Evidence

- 2026-09-22 update: current model evidence was refreshed from read-only
  discovery (232 current catalog rows; 32 static-eligible, 5 runtime-unknown in
  the static diagnostic). Four exact qualified/no-charge L2 identities entered
  a fresh bounded Host-process pool; three distinct quota domains returned
  confirmed `provider_unavailable`, so no Planner proposal, child Task, Critic
  invocation, Worker, Reviewer, or integration followed. The earlier UNKNOWN
  operation was not replayed. See
  `spec/v2/evidence/model-evidence-refresh-r9-planner-blocker-20260922.json`.
  R9 remains open; resume with a newly eligible route and fresh request
  identity. AR1 remains conditional on a natural FORMAT/PATCH or SEMANTIC/TEST
  failure and is not a precondition for R9.
- [ ] Preflight a fresh root with current qualified, no-charge provider routes;
  do not reuse historical UNKNOWN operations or old attempts.
- 2026-09-17 historical observation: the fresh Host-process L2 preflight reached qualified
  admission, but two new `gemini:worker:free-3` model attempts returned
  `provider_unavailable` before a response. This is recorded as an external
  blocker; the R9 completion checkbox remains open and no prior UNKNOWN
  operation is replayed.
- Local diagnostics follow-up: Provider-preflight `no_route` and resource-pool
  composition failures now project as bounded `blocked_local` results with
  `reconciliation_required=false`; this does not change the live
  `provider_unavailable` blocker or authorize a retry.
- [ ] Run live Planner, at least two non-overlapping Implementers, independent
  Reviewer, Host Verification, deterministic integration, dependent
  continuation, and exact-head CI.
- [ ] If a natural FORMAT/PATCH or SEMANTIC/TEST failure occurs, execute exactly
  one R4/R6 bounded recovery action; never manufacture a failure.
- [ ] Preserve truthful blocker categories for provider/network/quota/UNKNOWN
  outcomes and continue independent local work where possible.
- [ ] Create a redacted single-root evidence record; only promote Phase 8 Live
  after all required evidence exists.

## R10 — Weak-Model Convergence Benchmark

- [ ] After live activation, define a small reproducible set of planning,
  bounded bugfix, patch failure, semantic failure, two-file contract,
  Reviewer REWORK, and temporary provider-availability scenarios.
- [ ] Record first-pass rate, convergence rate, rounds, repeated signatures,
  escalation, human intervention, calls/tokens, elapsed time, Host Verification,
  provider failures, UNKNOWN outcomes, and rollback counts.
- [ ] Keep unauthorized mutation, UNKNOWN blind replay, unverified integration,
  and protected authority violation at zero.
- [ ] Keep benchmark evaluator proposal-only and separate from promotion
  authority; preserve existing Operation/Review/Gate ownership.

## Verification and Delivery for Each Code Slice

- [x] Run focused tests first and capture the output.
- [x] Run `python scripts/check_architecture.py`.
- [x] Run `python -m compileall -q src recovery scripts`.
- [x] Run `python -m pytest tests/v2 -q`.
- [x] Inspect the diff and update only the owning Current State/traceability or
  evidence documents when implementation evidence changes.
- [ ] Commit the verified slice and push `v2/bootstrap` to `origin`.
- [ ] Confirm exact-head CI for kernel 3.10, kernel 3.11, and provider-smoke.
- [ ] Report the pre-push commit separately from the post-push remote HEAD;
  never write the documentation-sync commit's own SHA into itself.

## Deferred Lane

The following remain out of the active convergence path: Task Scheduler/
Windows Service/OS Guardian deployment, deployed recovery, paid-provider
qualification, G6O1, GitHub bypass redesign, authority-state directory/ACL
migration, production auto-deploy, Discord/UI implementation, and any new
framework that duplicates existing scheduling, state, retry, or agent
authority.
