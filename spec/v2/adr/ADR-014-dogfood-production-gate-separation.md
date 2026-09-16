# ADR-014: Separate D9 Dogfood from Production Deployment

## Status

Accepted for roadmap and gate reporting; no automatic self-repair authority is
granted by this decision.

## Context

The D9 candidate and local Guardian composition can be exercised in an isolated
runtime, while OS-owned Guardian registration and deployed crash/reboot
recovery are not currently available. Treating both as one gate makes a local
dogfood repair permanently dependent on Task Scheduler and obscures which
evidence is missing.

## Decision

Use two explicit tracks:

- `D9_DOGFOOD`: one bounded Human-approved real repair with Host verification,
  pinned local runtime, health, and local rollback;
- `D9_PRODUCTION_DEPLOYMENT`: OS liveness, deployed recovery, rolling
  replacement, and deployed rollback.

The production track remains `DEFERRED_NOT_READY` while Task Scheduler is
deferred. The dogfood track exposes an additive `execution_readiness` state for
one bounded local trial, but remains `NOT_READY` until a real repair and
rollback evidence exists. `READY_FOR_TRIAL` is not a Gate promotion or an
approval consumption. Phase 8 preparation and live activation are also
reported separately; live activation requires the verified dogfood exit Gate in
addition to its own live benchmark evidence.

## Consequences

Local D9 trial preparation and Phase 8 benchmark preparation can proceed
without claiming OS deployment readiness. No existing Gate is weakened, and no
production or official-runtime mutation is implied by execution readiness or a
dogfood result.

## Rejected alternative

Do not relabel the existing D9 gate as ready merely because OS registration is
deferred, and do not create a second approval, scheduler, or recovery authority
to bypass the missing evidence.
