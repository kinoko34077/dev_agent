# #71 Canonical Operation/DevFarm execution lifecycle

## Goal

Remove duplicate lifecycle meaning between the durable Operation `Task` and
the development-only Commander/DevFarm plan without adding a scheduler,
second queue, or a new authority.  Operation remains the canonical durable
execution owner; DevFarm remains the isolated worker backend/projection.

## Audit findings

- Operation planner children are durable `Task` records and are parked at
  `WAITING_DEPENDENCY` for the DevFarm handoff.  The Operation record is only
  updated with the integration fact after the DevFarm path completes.
- Commander plan tasks independently advance through `READY`, `DISPATCHED`,
  `PROPOSED`, `HOST_VERIFIED`, and `INTEGRATED` while retaining separate
  attempt/result/review fields.
- The current bridge joins the two projections by `proposal_id + child_key`
  and DevFarm task id.  This is safe correlation, but it leaves the lifecycle
  meaning duplicated and cannot express restart-safe canonical stage identity.
- Host Verification, Reviewer, deterministic Integration, protected-path
  validation, worktree/manifest isolation, and Human/Gate authority are
  distinct authorities and must remain so.

## Bounded implementation slices

1. Add a provider-neutral canonical execution identity and lifecycle stage
   vocabulary beside the existing #68 request/route model.  Do not add a
   second Task status enum; lifecycle stages are a projection of the existing
   Operation `TaskStatus` and DevFarm backend stage.
2. Add an immutable, bounded execution binding to the Operation planner child
   metadata and DevFarm plan/manifest.  The binding carries the stable
   logical Operation task id, executor kind, proposal/child correlation, and
   current attempt/result/review/integration references.
3. Add a deterministic mapping/validation boundary used by the production
   composition and Commander paths.  Replayed identical backend observations
   are no-ops; conflicting identity or terminal evidence fails closed.
4. Add focused tests for identity propagation, stage mapping, duplicate
   observation idempotency, restart reconstruction, and preserved authority
   separation.  Existing legacy plans/manifests remain readable through an
   explicit legacy projection path.
5. Update #71 and Current State only after the slice is verified.  Decide at
   that checkpoint whether the remaining work is the canonical-state write
   path or whether #71 acceptance conditions are all met.

## Ownership and safety

- Codex owns this slice because it is cross-cutting and touches lifecycle,
  recovery, queue/lease, Host Verification, and integration boundaries.
- No provider call, Cloudflare replay, Gemini polling, paid fallback, Gate
  mutation, credential change, release/deploy, or destructive history action.
- The first verification is the affected focused suite.  Full regression and
  exact-head CI are required at the #71 acceptance boundary, not repeated for
  every mechanical sub-edit.
