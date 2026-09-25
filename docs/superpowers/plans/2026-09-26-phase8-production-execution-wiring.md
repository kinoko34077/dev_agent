# Phase 8 production execution wiring

## Goal

Connect the already verified Operation-to-DevFarm handoff to one bounded
execution pass that returns Host integration evidence through the existing
Operation dependency-release boundary.

## Constraints

- Reuse `CodexSupervisedCommanderRun` for dispatch, Host Verification, review
  decision persistence, and deterministic integration.
- Reuse `OperationService.record_devfarm_integration_evidence()` for the
  cross-plane evidence return and continuation release.
- Do not add a scheduler, queue, state store, retry engine, or approval
  authority.
- Validate all child identities before any review or integration mutation.
- Keep Phase 8 LIVE_ACTIVATION and D9 Production Deployment unchanged.

## Slice

1. Add RED tests for one bounded executor pass and idempotent resume after an
   integration boundary.
2. Implement a narrow `Phase8ProductionExecutor` with injected Worker
   providers/orchestrator and a final review-decision boundary.
3. Project only bounded run, worker, and continuation state; do not expose
   provider payloads or credentials.
4. Run focused tests, affected regression, compile/diff checks, and push the
   branch for exact-head CI.

## Explicit non-goals

Planner model admission, live remote availability, Discord live E2E, OS
deployment, and formal Phase 8 promotion remain separate roadmap gates.
