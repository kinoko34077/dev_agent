# Current State

## Exact current baseline

| Field | Value |
| --- | --- |
| Branch | `v2/bootstrap` |
| Implementation/evidence baseline | `e77725eaa33d0ff2d91da31d80fee15fac72ccb9` |
| Worktree | clean at the current evidence/test-hardening commit; later documentation-only synchronization may advance HEAD |
| Local regression | `945 passed, 1 skipped` (`python -m pytest tests/v2 -q`) |
| Architecture | `ARCHITECTURE_PASS` |
| Compile | `python -m compileall -q src recovery scripts` PASS |
| Exact-head CI | `v2-core` PASS [run 34754776566](https://github.com/kinoko34077/dev_agent/actions/runs/34754776566); `v2-provider-smoke` PASS [run 34754776538](https://github.com/kinoko34077/dev_agent/actions/runs/34754776538) |
| Gate source | `spec/v2/GATE_STATUS.json` and exact-head external CI; this document does not promote a Gate |

The detailed pre-consolidation snapshot is preserved at [`docs/archive/current-state/2026-09-13-pre-consolidation.md`](archive/current-state/2026-09-13-pre-consolidation.md).

## Verified capabilities

- Group D boundary: `client_session_key`, provider `external_session_id`, bounded artifact references, replay/reconciliation projection, and explicit `BackendDiscoveryAuthority` with identity/fingerprint checks. Automatic external session discovery is not claimed.
- Daily Supervisor path: Free L1 Worker dispatch, Host Verification, compact ReviewPacket, durable ReviewDecision, REWORK manifest, dependency release, and deterministic Host integration have existing evidence.
- Planner code boundary: exact L2 routing composition, strict JSON parsing, proposal-only `ModelPlanningAdapter`, `RootPlanningProposal`, Host-only `DevelopmentPlanningBridge`, and `CODE_INTEGRATED` preservation. Unsupported development dependency types remain fail-closed.
- MCP: schema-only operation contracts exist; no runtime adapter or transport is connected.
- Capability probes: bounded fixed probes exist as measurement evidence only; they do not promote Provider qualification or routing authority.

## Partially implemented / not verified

- Real Free L2 Planner success is `NOT VERIFIED`. Two separately bounded observations reached `gemini:core` / `gemini-3.8-flash`, and both stopped before proposal generation because provider availability returned HTTP 503 high-demand. No automatic retry storm is allowed. Evidence: [`planner-live-observation-20260913.json`](../spec/v2/evidence/planner-live-observation-20260913.json), [`planner-live-observation-20260913-02.json`](../spec/v2/evidence/planner-live-observation-20260913-02.json).
- The complete live chain `Free L2 proposal → Host validation → Bridge → Commander Plan → Free L1 Worker → Host Verification → Codex review → Host integration` is not yet evidenced as one Planner-originated development slice.
- Codex direct implementation still accounts for most recent repository changes. Worker-first delegation must be used for the next narrow slice; target-child Codex implementation invalidates that E2E evidence.
- Concrete Codex external-session restart/discovery is not verified. Without an explicit discovery authority, recovery remains `UNKNOWN`/reconciliation rather than inferred resume.
- MCP runtime, Free L2 Reviewer shadow, Codex-less cycle, and Self-Improvement F0–F2 are roadmap work, not current capability.

## External and frozen

- G6O1 is `DEFERRED_FROZEN`, `NOT VERIFIED`, and `roadmap_blocking=false`. Original paid-provider, worst-case billing, and deployment-owned budget requirements remain in [`spec/v2/G6O1_DEFERRED.md`](../spec/v2/G6O1_DEFERRED.md) and [`spec/v2/GATE_STATUS.json`](../spec/v2/GATE_STATUS.json).
- Compression Service, OpenAI API, Claude API, real paid-provider qualification, OS-level sandbox evidence, Production auto-deploy, Discord, and Virtual Office UI are not connected.
- Static Host Verification remains contained host execution, not an OS filesystem/network sandbox.

## Current blockers and boundaries

- D1/D2 require a bounded live external Planner observation and its exact evidence. Availability, transport, output syntax, schema, and Host validation failures must remain separate categories. The latest two D1 attempts are provider-availability blockers, not Planner-schema results; no third automatic attempt is scheduled.
- Provider/API credentials and external payload transmission are not inferred from repository code. A live request requires an explicit approved payload/destination boundary; otherwise local fail-closed work continues.
- Existing `.devfarm` operational artifacts may contain stale READY or rejected runs. A matching unfinished run is resumed only when its objective is still active; an already integrated objective is marked superseded operationally, not duplicated.
- Branch protection and required checks are external GitHub controls; exact-head CI is evidence, not a substitute for configuration.

## Immediate next target

1. Keep D0 documentation consolidation and its exact-head CI evidence current; the current implementation/evidence baseline is `e77725e`.
2. Do not automatically repeat the D1 live observation after the two provider-availability 503 results.
3. When provider availability permits and a separately approved bounded observation is requested, continue through D1; only then attempt D2: Bridge → Commander Plan → known qualified L1 Worker → Host Verification → Codex review-only → Host integration.

The active working checklist is [`docs/superpowers/plans/2026-09-13-planner-to-worker-e2e.md`](superpowers/plans/2026-09-13-planner-to-worker-e2e.md). The ordered roadmap is [`docs/V2_DETAILED_ROADMAP.md`](V2_DETAILED_ROADMAP.md).

## Evidence index

- Planner shadow: [`spec/v2/evidence/planner-shadow-20260913.json`](../spec/v2/evidence/planner-shadow-20260913.json)
- Latest bounded live observations: [`spec/v2/evidence/planner-live-observation-20260913.json`](../spec/v2/evidence/planner-live-observation-20260913.json), [`spec/v2/evidence/planner-live-observation-20260913-02.json`](../spec/v2/evidence/planner-live-observation-20260913-02.json)
- Worker Planner-adapter regression: [`spec/v2/evidence/planner-adapter-regression-20260913.json`](../spec/v2/evidence/planner-adapter-regression-20260913.json)
- Daily Supervisor: [`spec/v2/evidence/daily-supervisor-20260913.json`](../spec/v2/evidence/daily-supervisor-20260913.json)
- Existing qualification and provider evidence: [`spec/v2/evidence/`](../spec/v2/evidence/)
- Requirement traceability: [`spec/v2/TRACEABILITY.md`](../spec/v2/TRACEABILITY.md)
