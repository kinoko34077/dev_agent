# Current State

## Exact current baseline

| Field | Value |
| --- | --- |
| Branch | `v2/bootstrap` |
| Implementation/evidence baseline | `4b19716` (D1 live Planner proposal, D2 Worker integration evidence, and Python 3.11 compatibility fix) |
| Worktree | clean after the synchronized D2 evidence/documentation commit |
| Local regression | `993 passed, 1 skipped` (`python -m pytest tests/v2 -q`, 284.59s) |
| Architecture | `ARCHITECTURE_PASS` |
| Compile | `python -m compileall -q src recovery scripts` PASS |
| Exact-head CI | The post-D2 `c036ff4` run had `v2-provider-smoke` PASS and `v2-core` fail during Python 3.11 collection because of the model-catalog dataclass default; fixed in `4b19716`, whose exact-head CI is pending |
| Gate source | [`spec/v2/GATE_STATUS.json`](../spec/v2/GATE_STATUS.json) and exact-head external CI; this document does not promote a Gate |

The detailed pre-consolidation snapshot is preserved at [`docs/archive/current-state/2026-09-13-pre-consolidation.md`](archive/current-state/2026-09-13-pre-consolidation.md).

## Verified capabilities

- Group D boundary: `client_session_key`, provider `external_session_id`, bounded artifact references, replay/reconciliation projection, and explicit `BackendDiscoveryAuthority` with identity/fingerprint checks. Automatic external session discovery is not claimed.
- Daily Supervisor path: Free L1 Worker dispatch, Host Verification, compact ReviewPacket, durable ReviewDecision, REWORK manifest, dependency release, and deterministic Host integration have existing evidence.
- Planner code boundary: exact routing composition, strict JSON parsing, proposal-only `ModelPlanningAdapter`, `RootPlanningProposal`, Host-only `DevelopmentPlanningBridge`, and `CODE_INTEGRATED` preservation. Unsupported development dependency types remain fail-closed.
- Model evidence boundary: explicit read-only provider model discovery, exact Model Catalog and alias mapping, time-bounded external Benchmark Catalog with data-defined L1/L2/L3 thresholds and separate task-fit scores, canonical Capability Catalog, and Host-only `ModelAdmissionResolver`. Discovery, benchmark, capability, qualification, billing, privacy, quota, and health are not collapsed into one authority.
- Operation integration: the four model-evidence layers are loaded only when `DEV_AGENT_MODEL_EVIDENCE_DIR` is explicitly configured; default Operation behavior remains unchanged. `spec/v2/model_evidence/**` is protected from Worker changes.
- Planner resource boundary: exact current high-confidence qualification, benchmark-derived L2 admission, trusted no-charge billing, bounded `ProviderDispatcher` failover, structured `pool_exhausted` reporting, and no automatic L1 downgrade. The represented Gemini L2 identities are `gemini:core` and `gemini:worker:free-3/-4/-5`; free-3/-4/-5 have text-only unknown-quota qualification evidence and no tool-roundtrip claim.
- D1 live Planner evidence: a real Free L2 request selected `gemini:worker:free-3` / `gemini-3.6-flash`, decoded strict JSON, built one `RootPlanningProposal`, and passed Host validation through the qualified pool. See [`planner-l2-live-d1-20260914.json`](../spec/v2/evidence/planner-l2-live-d1-20260914.json).
- D2 live development evidence: that Planner proposal passed the Host-only DevelopmentPlanningBridge, created a Commander Plan, delegated a narrow child to `gemini:worker` / `gemini-3.5-flash-lite`, passed independent Host Verification, received a durable Codex `APPROVE_INTEGRATION`, and was integrated by the deterministic Host helper. The target child had zero Codex direct implementation. See [`planner-to-worker-e2e-20260914.json`](../spec/v2/evidence/planner-to-worker-e2e-20260914.json).
- MCP: schema-only operation contracts exist; no runtime adapter or transport is connected.

## Partially implemented / not verified

- The earlier bounded D1 pool observation remains a truthful failure record: `gemini:worker:free-4` and `gemini:worker:free-5` returned confirmed `provider_unavailable` and the pool ended `pool_exhausted` without a proposal. A later bounded run succeeded through `gemini:worker:free-3`; no retry storm, UNKNOWN failover, or L1 downgrade occurred. See [`planner-l2-pool-observation-20260914.json`](../spec/v2/evidence/planner-l2-pool-observation-20260914.json) and [`planner-l2-live-d1-20260914.json`](../spec/v2/evidence/planner-l2-live-d1-20260914.json).
- The model catalog refresh observed `1289` model entries across `12/14` configured bindings. `groq` returned `HTTPError` and local `ollama` returned `URLError`; these are recorded as bounded discovery failures, not inferred availability. See [`model-catalog-refresh-20260914.json`](../spec/v2/evidence/model-catalog-refresh-20260914.json).
- D1/D2 are verified locally for the bounded Planner-originated development slice. The evidence includes bounded prior failures (transport, patch format, and independent trusted-target policy) and the successful retry; this is not a claim of broad Worker reliability.
- D3 Worker reliability hardening is conditional: investigate only if the observed patch/manifest failure classes recur. The validator remains fail-closed.
- Concrete Codex external-session restart/discovery is not verified. Without an explicit discovery authority, recovery remains `UNKNOWN`/reconciliation rather than inferred resume.
- MCP runtime, Free L2 Reviewer shadow, Codex-less cycle, and Self-Improvement F0–F2 are roadmap work, not current capability.

## External and frozen

- G6O1 is `DEFERRED_FROZEN`, `NOT VERIFIED`, and `roadmap_blocking=false`. Original paid-provider, worst-case billing, and deployment-owned budget requirements remain in [`spec/v2/G6O1_DEFERRED.md`](../spec/v2/G6O1_DEFERRED.md) and [`spec/v2/GATE_STATUS.json`](../spec/v2/GATE_STATUS.json).
- Compression Service, OpenAI API, Claude API, real paid-provider qualification, OS-level sandbox evidence, Production auto-deploy, Discord, and Virtual Office UI are not connected.
- Static Host Verification remains contained host execution, not an OS filesystem/network sandbox.

## Current blockers and boundaries

- D1/D2 live evidence is complete for the bounded slice above. Availability, transport, output syntax, schema, and Host validation failures remain separate categories; do not launch unbounded retries or silently downgrade to L1. The first post-D2 push exposed a Python 3.11-only collection defect; it is fixed by `4b19716` and does not change runtime semantics.
- Model discovery is observation only. An API-listed model is not routable without exact current benchmark, capability, qualification, billing, privacy, quota, health, and explicit binding evidence.
- Free-3/-4/-5 qualification used a bounded text-only bootstrap because their trusted allowance quota telemetry was unavailable. This does not establish tool-call qualification or numeric headroom.
- Existing `.devfarm` operational artifacts may contain stale READY or rejected runs. A matching unfinished run is resumed only when its objective is still active; an already integrated objective is marked superseded operationally, not duplicated.
- GitHub branch protection and required checks remain external configuration/evidence; the repository ruleset and operator-authorized direct push do not turn local evidence into unattended promotion.

## Immediate next target

1. Keep D3 conditional and bounded: use the recorded failure categories to decide whether a narrow Worker transport/patch-contract correction is warranted; do not weaken validation.
2. Proceed to D4 concrete Codex session restart/discovery using explicit discovery authority only; otherwise preserve UNKNOWN/reconciliation semantics.
3. After D4 boundary evidence, implement the existing schema-only MCP contract as a thin runtime adapter. Do not connect Compression, paid providers, or frozen G6O1 work.

The completed working checklist is archived at [`docs/archive/plans/2026-09/2026-09-13-planner-to-worker-e2e.md`](archive/plans/2026-09/2026-09-13-planner-to-worker-e2e.md). The ordered roadmap is [`docs/V2_DETAILED_ROADMAP.md`](V2_DETAILED_ROADMAP.md).

## Evidence index

- Model catalog refresh: [`spec/v2/evidence/model-catalog-refresh-20260914.json`](../spec/v2/evidence/model-catalog-refresh-20260914.json)
- Planner L2 pool observation: [`spec/v2/evidence/planner-l2-pool-observation-20260914.json`](../spec/v2/evidence/planner-l2-pool-observation-20260914.json)
- Gemini free binding qualification: [`gemini-l2-qualification-free-3-20260914.json`](../spec/v2/evidence/gemini-l2-qualification-free-3-20260914.json), [`gemini-l2-qualification-free-4-20260914.json`](../spec/v2/evidence/gemini-l2-qualification-free-4-20260914.json), [`gemini-l2-qualification-free-5-20260914.json`](../spec/v2/evidence/gemini-l2-qualification-free-5-20260914.json)
- Reviewed model evidence snapshots: [`spec/v2/model_evidence/`](../spec/v2/model_evidence/)
- Planner live D1 / D2 development evidence: [`planner-l2-live-d1-20260914.json`](../spec/v2/evidence/planner-l2-live-d1-20260914.json), [`planner-to-worker-e2e-20260914.json`](../spec/v2/evidence/planner-to-worker-e2e-20260914.json)
- Existing Supervisor and Planner evidence: [`spec/v2/evidence/`](../spec/v2/evidence/)
- Requirement traceability: [`spec/v2/TRACEABILITY.md`](../spec/v2/TRACEABILITY.md)
