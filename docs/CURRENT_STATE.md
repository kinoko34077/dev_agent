# Current State

## Exact current baseline

| Field | Value |
| --- | --- |
| Branch | `v2/bootstrap` |
| Implementation/evidence baseline | `956122b` (`model discovery/admission` plus additional exact Gemini L2 binding qualification) |
| Worktree | clean at the model-evidence and Planner-pool documentation sync commit |
| Local regression | `977 passed, 1 skipped` (`python -m pytest tests/v2 -q`) |
| Architecture | `ARCHITECTURE_PASS` |
| Compile | `python -m compileall -q src recovery scripts` PASS |
| Exact-head CI | `v2-core` PASS [run 34769584761](https://github.com/kinoko34077/dev_agent/actions/runs/34769584761) and `v2-provider-smoke` PASS [run 34769584772](https://github.com/kinoko34077/dev_agent/actions/runs/34769584772) for implementation/evidence baseline `547c5ef`; any later commit requires its own exact-head check |
| Gate source | [`spec/v2/GATE_STATUS.json`](../spec/v2/GATE_STATUS.json) and exact-head external CI; this document does not promote a Gate |

The detailed pre-consolidation snapshot is preserved at [`docs/archive/current-state/2026-09-13-pre-consolidation.md`](archive/current-state/2026-09-13-pre-consolidation.md).

## Verified capabilities

- Group D boundary: `client_session_key`, provider `external_session_id`, bounded artifact references, replay/reconciliation projection, and explicit `BackendDiscoveryAuthority` with identity/fingerprint checks. Automatic external session discovery is not claimed.
- Daily Supervisor path: Free L1 Worker dispatch, Host Verification, compact ReviewPacket, durable ReviewDecision, REWORK manifest, dependency release, and deterministic Host integration have existing evidence.
- Planner code boundary: exact routing composition, strict JSON parsing, proposal-only `ModelPlanningAdapter`, `RootPlanningProposal`, Host-only `DevelopmentPlanningBridge`, and `CODE_INTEGRATED` preservation. Unsupported development dependency types remain fail-closed.
- Model evidence boundary: explicit read-only provider model discovery, exact Model Catalog and alias mapping, time-bounded external Benchmark Catalog with data-defined L1/L2/L3 thresholds and separate task-fit scores, canonical Capability Catalog, and Host-only `ModelAdmissionResolver`. Discovery, benchmark, capability, qualification, billing, privacy, quota, and health are not collapsed into one authority.
- Operation integration: the four model-evidence layers are loaded only when `DEV_AGENT_MODEL_EVIDENCE_DIR` is explicitly configured; default Operation behavior remains unchanged. `spec/v2/model_evidence/**` is protected from Worker changes.
- Planner resource boundary: exact current high-confidence qualification, benchmark-derived L2 admission, trusted no-charge billing, bounded `ProviderDispatcher` failover, structured `pool_exhausted` reporting, and no automatic L1 downgrade. The represented Gemini L2 identities are `gemini:core` and `gemini:worker:free-3/-4/-5`; free-3/-4/-5 have text-only unknown-quota qualification evidence and no tool-roundtrip claim.
- MCP: schema-only operation contracts exist; no runtime adapter or transport is connected.

## Partially implemented / not verified

- D1 real Free L2 Planner success is `NOT VERIFIED`. The latest bounded pool observation used `gemini:worker:free-4` and `gemini:worker:free-5`; both returned confirmed `provider_unavailable`, so the pool ended as `pool_exhausted` without a proposal or Host validation. No automatic retry storm, UNKNOWN failover, or L1 downgrade occurred. See [`planner-l2-pool-observation-20260914.json`](../spec/v2/evidence/planner-l2-pool-observation-20260914.json).
- The model catalog refresh observed `1289` model entries across `12/14` configured bindings. `groq` returned `HTTPError` and local `ollama` returned `URLError`; these are recorded as bounded discovery failures, not inferred availability. See [`model-catalog-refresh-20260914.json`](../spec/v2/evidence/model-catalog-refresh-20260914.json).
- The complete live chain `Free L2 proposal → Host validation → Bridge → Commander Plan → Free L1 Worker → Host Verification → Codex review → Host integration` is not yet evidenced as one Planner-originated development slice. D2 remains pending on D1.
- Concrete Codex external-session restart/discovery is not verified. Without an explicit discovery authority, recovery remains `UNKNOWN`/reconciliation rather than inferred resume.
- MCP runtime, Free L2 Reviewer shadow, Codex-less cycle, and Self-Improvement F0–F2 are roadmap work, not current capability.

## External and frozen

- G6O1 is `DEFERRED_FROZEN`, `NOT VERIFIED`, and `roadmap_blocking=false`. Original paid-provider, worst-case billing, and deployment-owned budget requirements remain in [`spec/v2/G6O1_DEFERRED.md`](../spec/v2/G6O1_DEFERRED.md) and [`spec/v2/GATE_STATUS.json`](../spec/v2/GATE_STATUS.json).
- Compression Service, OpenAI API, Claude API, real paid-provider qualification, OS-level sandbox evidence, Production auto-deploy, Discord, and Virtual Office UI are not connected.
- Static Host Verification remains contained host execution, not an OS filesystem/network sandbox.

## Current blockers and boundaries

- D1/D2 require a bounded live external Planner observation and exact evidence. Availability, transport, output syntax, schema, and Host validation failures remain separate categories. The latest availability failure exhausted the currently attempted L2 pool; do not launch unbounded retries or silently downgrade to L1.
- Model discovery is observation only. An API-listed model is not routable without exact current benchmark, capability, qualification, billing, privacy, quota, health, and explicit binding evidence.
- Free-3/-4/-5 qualification used a bounded text-only bootstrap because their trusted allowance quota telemetry was unavailable. This does not establish tool-call qualification or numeric headroom.
- Existing `.devfarm` operational artifacts may contain stale READY or rejected runs. A matching unfinished run is resumed only when its objective is still active; an already integrated objective is marked superseded operationally, not duplicated.
- GitHub branch protection and required checks remain external configuration/evidence; the repository ruleset and operator-authorized direct push do not turn local evidence into unattended promotion.

## Immediate next target

1. After a provider availability change or a new exact current high-confidence L2 qualification, run one further bounded D1 pool observation; do not retry the exhausted Gemini pool in a storm.
2. If D1 returns a valid proposal, continue without a new human stop through D2: Bridge → Commander Plan → known qualified L1 Worker → Host Verification → Codex review-only → Host integration.
3. If D1 remains unavailable, keep the failure evidence and proceed only with the next non-blocked roadmap work; no automatic L1 Planner fallback.

The active working checklist is [`docs/superpowers/plans/2026-09-13-planner-to-worker-e2e.md`](superpowers/plans/2026-09-13-planner-to-worker-e2e.md). The ordered roadmap is [`docs/V2_DETAILED_ROADMAP.md`](V2_DETAILED_ROADMAP.md).

## Evidence index

- Model catalog refresh: [`spec/v2/evidence/model-catalog-refresh-20260914.json`](../spec/v2/evidence/model-catalog-refresh-20260914.json)
- Planner L2 pool observation: [`spec/v2/evidence/planner-l2-pool-observation-20260914.json`](../spec/v2/evidence/planner-l2-pool-observation-20260914.json)
- Gemini free binding qualification: [`gemini-l2-qualification-free-3-20260914.json`](../spec/v2/evidence/gemini-l2-qualification-free-3-20260914.json), [`gemini-l2-qualification-free-4-20260914.json`](../spec/v2/evidence/gemini-l2-qualification-free-4-20260914.json), [`gemini-l2-qualification-free-5-20260914.json`](../spec/v2/evidence/gemini-l2-qualification-free-5-20260914.json)
- Reviewed model evidence snapshots: [`spec/v2/model_evidence/`](../spec/v2/model_evidence/)
- Existing Supervisor and Planner evidence: [`spec/v2/evidence/`](../spec/v2/evidence/)
- Requirement traceability: [`spec/v2/TRACEABILITY.md`](../spec/v2/TRACEABILITY.md)
