# Planner-to-Worker live integration plan

Status: ACTIVE. Canonical detailed order: `docs/V2_DETAILED_ROADMAP.md`.

## Scope

Close D1/D2 only: one bounded live Free L2 proposal, Host validation, DevelopmentPlanningBridge, Commander Plan, one qualified Free L1 Worker child, Host Verification, Codex review-only, and deterministic Host integration.

## Checklist

- [ ] Reconfirm HEAD, dirty state, existing unfinished plan, qualification, and external approval boundary.
- [x] Add explicit qualified-L2 planner pool composition, failover-safe ProviderError semantics, Gemini confirmed-unavailable classification, and bounded pool-exhaustion evidence.
- [ ] Perform at most one explicitly bounded live L2 observation; classify transport/provider/output/schema/Host failures separately.
- [ ] Persist proposal digest and Host validation result without granting Planner authority.
- [ ] Convert only a validated proposal through the existing DevelopmentPlanningBridge.
- [ ] Create the Commander Plan through the existing Host boundary; preserve `CODE_INTEGRATED` and reject unsupported dependency types.
- [ ] Assign a narrow child to `gemini:worker` / `gemini-3.5-flash-lite` or another currently qualified L1 binding.
- [ ] Keep Codex direct implementation count for the target child at zero.
- [ ] Run Supervisor, Host Verification, ReviewPacket, durable review, and `integrate_approved_worker()`.
- [ ] Record evidence without credentials/raw conversation.
- [ ] Run focused tests, full `tests/v2`, architecture, compileall, push, and exact-head CI.

Current note: local pool/failover and fake Planner proposal → Host validation are verified at `c9789b2`; the current matrix contains one exact high-confidence L2 identity. A new live Planner payload was not sent because the external-send review rejected transmission of the internal objective and repository/branch context. D1 remains `NOT VERIFIED`; D2 must not be claimed until a live valid proposal exists.

## Non-goals

No Planner authority, automatic retry storm, new scheduler, new state machine, MCP runtime, Compression Service, G6O1 promotion, paid-provider work, or Codex fallback implementation for the target child.
