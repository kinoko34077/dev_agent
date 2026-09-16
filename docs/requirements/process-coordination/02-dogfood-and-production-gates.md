# Dogfood Gate and Production Deployment Gate

## Purpose

The development roadmap contains two different operational claims that must not
share one completion gate:

1. A bounded, Human-approved self-repair can be exercised in an isolated/local
   runtime with Host verification and a proven rollback path.
2. A long-lived production runtime is supervised by an OS-owned Guardian and
   can recover from process, reboot, and deployed rolling/rollback failures.

The first is `D9_DOGFOOD`. The second is `D9_PRODUCTION_DEPLOYMENT`.

## D9_DOGFOOD

`D9_DOGFOOD` has two distinct states. Its execution readiness may be
`READY_FOR_TRIAL` without Task Scheduler, Windows Service, or other OS
registration when the local prerequisites are verified. Its gate status remains
`NOT_READY` until the trial itself produces the required mutation and recovery
evidence. Execution readiness is therefore an authorization boundary for one
bounded trial, not a completed-gate claim.

The trial prerequisites are:

- a real production Worker path and Host Egress/Verification boundary;
- the existing proposal-only RepairCandidate policy;
- durable exact approval binding with consume-once semantics;
- a verified revision-pinned local runtime and local health transition; and
- the existing local rollback/recovery primitive with UNKNOWN/reconciliation
  preserved.

The exit gate still requires all of the following:

- a real bounded production-code repair for an existing issue;
- immutable Worker attempt and Egress evidence;
- independent Host Verification;
- durable approval bound to candidate, attempt, patch, manifest, target
  revision, operation type, and RollbackProof;
- revision-pinned local runtime promotion and health verification; and
- a verified local rollback and recovery result.

The transition is:

`READY_FOR_TRIAL` → one Human-approved bounded local trial → repair, health,
and rollback evidence → `D9_DOGFOOD=VERIFIED`.

The gate does not grant unrestricted self-repair, protected-path mutation,
automatic approval, official-branch Codex-less integration, or production
deployment authority.

## D9_PRODUCTION_DEPLOYMENT

`D9_PRODUCTION_DEPLOYMENT` remains not ready while OS-owned Guardian liveness,
deployed crash/reboot recovery, deployed rolling replacement, and deployed
rollback are not evidenced. Task Scheduler is currently deferred by explicit
Human direction. Its deferral must not be represented as a failure of the
local dogfood contract, and it must not be silently promoted to verified. This
track is independent from `D9_DOGFOOD` trial readiness and does not block one
bounded local dogfood trial.

## Phase 8 relationship

Phase 8 has two states:

- `PREPARATION_ONLY`: role contracts, ownership/lease projection, local
  deterministic multi-role composition, and Host boundaries are verified;
- `LIVE_ACTIVATION`: real Provider multi-role work has produced independent
  verified patches, review evidence, deterministic integration, and exact-head
  CI for the benchmark task, and `D9_DOGFOOD=VERIFIED` is satisfied.

`PREPARATION_ONLY` is not Phase 8 application activation. `LIVE_ACTIVATION`
does not grant Reviewer, Worker, MCP, or UI authority beyond existing Host and
Human boundaries.

## Authority

This split is a reporting and dependency boundary. It does not weaken existing
Human approval, Host Verification, protected paths, Recovery, budget/privacy,
UNKNOWN/reconciliation, or integration policy.
