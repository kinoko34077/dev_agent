# Phase 8 production submission boundary

## Goal

Make one ordinary Phase 8 production submission sufficient to compose the
already verified Operation planner handoff and the bounded DevFarm execution
pass. The caller may submit and observe; it must not sequence internal
Planner, Worker, Host Verification, Review, Integration, or dependency-release
steps.

## Constraints

- Reuse `OperationService`, `DevelopmentPlanningBridge`, `CodexSupervisedCommanderRun`,
  `Phase8ProductionExecutor`, and the existing StateStore/Queue boundaries.
- Keep provider/model responses injected at the external boundary only.
- Do not add a scheduler, queue, StateStore, retry engine, or authority.
- One call performs one bounded pass; incomplete work remains durable for a
  later existing supervisor/resume boundary.
- Keep Phase 8 LIVE_ACTIVATION and D9 Production Deployment unchanged.

## Slice

1. Add a RED test in which the test driver only calls the public submit and
   observe facade while the production submission boundary composes the
   existing authorities.
2. Implement a narrow submission object that creates one fresh Operation root,
   validates/applies the Planner proposal, builds the existing Commander plan,
   records the single-owner handoff, and invokes one existing executor pass.
3. Return only bounded root/run/proposal and execution projections.
4. Run the focused composition tests and affected identity/execution tests,
   then compile, commit, push, and inspect exact-head CI.

## Explicit non-goals

Live remote Provider admission, Discord live E2E, a persistent Phase 8
scheduler, automatic retry loops, OS deployment, and formal Gate promotion.
