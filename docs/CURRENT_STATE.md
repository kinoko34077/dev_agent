# Current State

## Exact current baseline

| Field | Value |
| --- | --- |
| Branch | `v2/bootstrap` |
| Implementation/evidence baseline | `ee23bfa` (D4 discovery boundary, fixed Compression client boundary, thin MCP Supervisor adapter, and synchronized evidence/docs) |
| Worktree | clean after the D4/D5/Compression synchronization commits |
| Local regression | `1007 passed, 1 skipped` (`python -m pytest tests/v2 -q`, 177.19s) |
| Architecture | `ARCHITECTURE_PASS` |
| Compile | `python -m compileall -q src recovery scripts` PASS |
| Exact-head CI | PASS for `ee23bfa`: `v2-core` [run 34795582218](https://github.com/kinoko34077/dev_agent/actions/runs/34795582218) and `v2-provider-smoke` [run 34795582184](https://github.com/kinoko34077/dev_agent/actions/runs/34795582184). |
| Gate source | [`spec/v2/GATE_STATUS.json`](../spec/v2/GATE_STATUS.json) and exact-head external CI; this document does not promote a Gate |

The pre-consolidation and earlier milestone snapshots remain in
[`docs/archive/`](archive/), while this document describes only the current
state. Detailed requirements and decisions stay in their owning documents.

## Verified capabilities

- Group D boundary: `client_session_key`, provider `external_session_id`, bounded artifact references, replay/reconciliation projection, and explicit `BackendDiscoveryAuthority` with identity/fingerprint checks. `CodexExecBackend` has no formal post-restart discovery mechanism; absent or mismatched explicit authority closes to `UNKNOWN` and does not guess from artifacts or thread IDs. See [`codex-session-restart-discovery-20260914.json`](../spec/v2/evidence/codex-session-restart-discovery-20260914.json).
- Daily Supervisor path: Free L1 Worker dispatch, Host Verification, compact ReviewPacket, durable ReviewDecision, REWORK manifest, dependency release, and deterministic Host integration have existing D1/D2 evidence.
- Planner/model evidence boundary: strict JSON proposal handling, Host-only planning validation/bridge, explicit Model Catalog, alias, Benchmark, Capability, qualification, billing, privacy, quota, and health separation, plus bounded same-tier failover. UNKNOWN outcomes remain reconciliation-only and L1 is not an automatic Planner downgrade.
- D1 live Planner evidence: a real Free L2 request selected `gemini:worker:free-3` / `gemini-3.6-flash`, decoded strict JSON, built one `RootPlanningProposal`, and passed Host validation through the qualified pool. See [`planner-l2-live-d1-20260914.json`](../spec/v2/evidence/planner-l2-live-d1-20260914.json).
- D2 live development evidence: that proposal passed `DevelopmentPlanningBridge`, created a Commander Plan, delegated a narrow child to `gemini:worker` / `gemini-3.5-flash-lite`, passed independent Host Verification, received durable Codex `APPROVE_INTEGRATION`, and was integrated by the deterministic Host helper. The target child had zero Codex direct implementation. See [`planner-to-worker-e2e-20260914.json`](../spec/v2/evidence/planner-to-worker-e2e-20260914.json).
- Compression boundary: `HttpCompressionService` uses the fixed endpoint/profile only when explicitly composed; `compress_handoff_payload()` keeps Control out of the request, applies the 3,000 Unicode code-point threshold to Payload after reference-first handling, validates provenance/digests, and has bounded fallback or fail-closed behavior.
- MCP boundary: `McpRuntimeAdapter` and development-only `SupervisorMcpBinding` delegate bounded operations to existing Supervisor authority. Connected operations are `status`, `artifact_summary`, `run`, `resume`, `review`, `rework`, and `integrate`. Wire transport and Planner proposal/apply authority remain unconnected. See [`mcp-runtime-adapter-20260914.json`](../spec/v2/evidence/mcp-runtime-adapter-20260914.json).

## Partially implemented / not verified

- Compression live availability is `NOT_VERIFIED`: the operator smoke reached the fixed client boundary but `COMPRESSION_API_TOKEN` was not present in the current environment, so no authenticated service result was claimed. See [`compression-service-connection-20260914.json`](../spec/v2/evidence/compression-service-connection-20260914.json). No token or raw response is stored.
- D4 same-operation resume is `NOT_AVAILABLE` for the concrete Codex backend without a formal external discovery API. The safe result is `UNKNOWN`/reconciliation, not inferred resume or blind restart.
- D5 is a transport-neutral in-process/development adapter. A network MCP server/wire transport and Planner mutation authority are not implemented.
- D3 Worker reliability remains conditional. Existing fail-closed patch/manifest/Host Verification boundaries are not broadened without recurrence evidence.
- Free L2 Reviewer shadow, Codex-less cycle, and Self-Improvement F0–F2 remain roadmap work. D6 still requires the existing multiple D2 success evidence condition.

## External and frozen

- G6O1 is `DEFERRED_FROZEN`, `NOT VERIFIED`, and `roadmap_blocking=false`. Original paid-provider, worst-case billing, and deployment-owned budget requirements remain in [`spec/v2/G6O1_DEFERRED.md`](../spec/v2/G6O1_DEFERRED.md) and [`spec/v2/GATE_STATUS.json`](../spec/v2/GATE_STATUS.json).
- OpenAI API, Claude API, real paid-provider qualification, OS-level sandbox evidence, Production auto-deploy, Discord, and Virtual Office UI remain outside the active roadmap until explicitly reopened. Compression is no longer frozen, but it is only a fixed payload optimization and never G6O1 billing/evidence.
- Static Host Verification remains contained host execution, not an OS filesystem/network sandbox.

## Current blockers and boundaries

- External Compression authentication and MCP wire transport are unverified/unconnected; neither is silently treated as complete.
- External API/session settle polling has no applicable D4 implementation currently. If a future external readiness/discovery poll is added, use approximately 6 seconds with finite attempts and a deadline. Do not change request/provider timeouts or UNKNOWN-effect resend semantics.
- Model discovery and benchmark evidence are observation inputs, not routing grants. Exact current qualification, capability, billing, privacy, quota, health, and binding admission remain required.
- The active `v2/bootstrap` GitHub ruleset requires `kernel (3.10)`, `kernel (3.11)`, and `provider-smoke`, and prevents deletion/non-fast-forward updates. The current authenticated direct-push identity is a configured bypass actor, so remote push acceptance does not replace exact-head CI evidence. No local artifact promotes a Gate.

## Immediate next target

1. Keep D3 conditional and bounded; fix only a recorded recurring failure class.
2. Preserve D4's explicit-discovery/UNKNOWN boundary; do not invent Codex session discovery.
3. Treat the D5 in-process adapter as the current boundary; wire transport and Planner mutation tools are separate future slices over the same authority.
4. Select the next non-blocking roadmap task without relaxing G6O1 or D6 dependencies.

The ordered roadmap is [`docs/V2_DETAILED_ROADMAP.md`](V2_DETAILED_ROADMAP.md),
and the compact operational procedure is [`docs/CODEX_DAILY_DOGFOOD.md`](CODEX_DAILY_DOGFOOD.md).

## Evidence index

- D4 Codex session discovery boundary: [`codex-session-restart-discovery-20260914.json`](../spec/v2/evidence/codex-session-restart-discovery-20260914.json)
- D5 MCP runtime adapter boundary: [`mcp-runtime-adapter-20260914.json`](../spec/v2/evidence/mcp-runtime-adapter-20260914.json)
- Compression client/live smoke boundary: [`compression-service-connection-20260914.json`](../spec/v2/evidence/compression-service-connection-20260914.json)
- Planner live D1 / D2 development evidence: [`planner-l2-live-d1-20260914.json`](../spec/v2/evidence/planner-l2-live-d1-20260914.json), [`planner-to-worker-e2e-20260914.json`](../spec/v2/evidence/planner-to-worker-e2e-20260914.json)
- Model catalog and pool observations: [`spec/v2/evidence/`](../spec/v2/evidence/)
- Requirement traceability: [`spec/v2/TRACEABILITY.md`](../spec/v2/TRACEABILITY.md)
