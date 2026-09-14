# Current State

## Exact current baseline

| Field | Value |
| --- | --- |
| Branch | `v2/bootstrap` |
| Current HEAD | `ec0632d` (`docs: sync current head and CI evidence`) |
| Implementation/evidence baseline | `a26c33c` (coordination/egress foundation plus deterministic provider-saturation regression fixture) |
| Worktree | clean at the current verification checkpoint; this documentation sync records the same checkpoint |
| Local regression | `1113 passed, 1 skipped` (`python -m pytest tests/v2 -q`, 186.38s) |
| Architecture | `ARCHITECTURE_PASS` |
| Compile | `python -m compileall -q src recovery scripts` PASS |
| Exact-head CI | PASS for current HEAD `ec0632d65203292af1bea06010e3769f52bda568`: `v2-core` [run 34839040270](https://github.com/kinoko34077/dev_agent/actions/runs/34839040270) and `v2-provider-smoke` [run 34839040257](https://github.com/kinoko34077/dev_agent/actions/runs/34839040257). |
| Gate source | [`spec/v2/GATE_STATUS.json`](../spec/v2/GATE_STATUS.json) and exact-head external CI; this document does not promote a Gate |

The pre-consolidation and earlier milestone snapshots remain in
[`docs/archive/`](archive/), while this document describes only the current
state. Detailed requirements and decisions stay in their owning documents.

## Verified capabilities

- Group D boundary: `client_session_key`, provider `external_session_id`, bounded artifact references, replay/reconciliation projection, and explicit `BackendDiscoveryAuthority` with identity/fingerprint checks. `CodexExecBackend` has no formal post-restart discovery mechanism; absent or mismatched explicit authority closes to `UNKNOWN` and does not guess from artifacts or thread IDs. See [`codex-session-restart-discovery-20260914.json`](../spec/v2/evidence/codex-session-restart-discovery-20260914.json).
- Daily Supervisor path: Free L1 Worker dispatch, Host Verification, compact ReviewPacket, durable ReviewDecision, REWORK manifest, dependency release, and deterministic Host integration have existing D1/D2 evidence. A second, different bounded documentation slice was also Planner-originated, Worker-integrated, and recorded with zero Codex direct implementation in [`d2-dogfood-doc-note-20260914.json`](../spec/v2/evidence/d2-dogfood-doc-note-20260914.json).
- Planner/model evidence boundary: strict JSON proposal handling, Host-only planning validation/bridge, explicit Model Catalog, alias, Benchmark, Capability, qualification, billing, privacy, quota, and health separation, plus bounded same-tier failover. UNKNOWN outcomes remain reconciliation-only and L1 is not an automatic Planner downgrade.
- D1 live Planner evidence: a real Free L2 request selected `gemini:worker:free-3` / `gemini-3.6-flash`, decoded strict JSON, built one `RootPlanningProposal`, and passed Host validation through the qualified pool. See [`planner-l2-live-d1-20260914.json`](../spec/v2/evidence/planner-l2-live-d1-20260914.json).
- D6 Reviewer Shadow evidence: a real Free L2 reviewer produced proposal-only `APPROVE_INTEGRATION` proposals from compact ReviewPackets for two distinct D2 Task/attempt samples. Both agreed with the durable Codex decision, used grounded evidence, and never wrote decision/integration state. Evidence is recorded in [`reviewer-shadow-20260914.json`](../spec/v2/evidence/reviewer-shadow-20260914.json) and [`reviewer-shadow-20260914-02.json`](../spec/v2/evidence/reviewer-shadow-20260914-02.json).
- D2 live development evidence: that proposal passed `DevelopmentPlanningBridge`, created a Commander Plan, delegated a narrow child to `gemini:worker` / `gemini-3.5-flash-lite`, passed independent Host Verification, received durable Codex `APPROVE_INTEGRATION`, and was integrated by the deterministic Host helper. The target child had zero Codex direct implementation. See [`planner-to-worker-e2e-20260914.json`](../spec/v2/evidence/planner-to-worker-e2e-20260914.json).
- Compression boundary: normal `OneCycleDevelopmentLoop` composition lazily builds `HttpCompressionService` only when `COMPRESSION_API_TOKEN` is present; import-time secret/network I/O remains absent. `compress_handoff_payload()` keeps Control out of the request, applies the 3,000 Unicode code-point threshold to Payload after reference-first handling, preserves provenance/digests, separates the 1,000,000 structural limit from the 200,000 provider-safe limit, and has bounded fallback or fail-closed behavior.
- MCP boundary: `McpRuntimeAdapter` and development-only `SupervisorMcpBinding` delegate bounded operations to existing Supervisor authority. Connected operations are `status`, `artifact_summary`, `run`, `resume`, `review`, `rework`, and `integrate`. Wire transport and Planner proposal/apply authority remain unconnected. See [`mcp-runtime-adapter-20260914.json`](../spec/v2/evidence/mcp-runtime-adapter-20260914.json).
- D7 bounded candidate boundary: `CodexLessPolicy` and the Supervisor read-only `codexless` CLI evaluate distinct clean Shadow evidence, Worker ownership, low/normal Task classification, scope/protected paths, independent Host Verification, trust/approval, and ReviewPacket grounding. A passing result is only a `CANDIDATE`; it does not grant Reviewer, official-branch, push, merge, Gate, or unconditional integration authority.
- D7 live candidate evidence: a real Free L1 Worker changed one bounded public documentation file, independent Host Verification passed, and a real Free L2 Reviewer produced a proposal-only `APPROVE_INTEGRATION` without a Codex decision. The Supervisor `codexless` policy returned `CANDIDATE`; no official-branch integration or auto-merge was performed. See [`d7-codexless-candidate-20260914.json`](../spec/v2/evidence/d7-codexless-candidate-20260914.json).
- D8 Host composition: `scripts/devfarm_self_improvement.py` reads a public Supervisor plan/ReviewPacket boundary and composes `observe → diagnose → plan` into immutable bounded artifacts under `.devfarm/self-improvement/`. The records remain evidence-grounded and proposal-only; no model call, automatic dispatch, Task mutation, repair, approval, or integration is connected. See [`d8-self-improvement-composition-20260914.json`](../spec/v2/evidence/d8-self-improvement-composition-20260914.json).
- D9 candidate boundary: `RepairPolicy` accepts only an F2 `PROPOSAL_ONLY` plan plus bounded Host evidence with independent verification, known external outcome, attempt-scoped trust/approval, safe paths, and a rollback reference. `RepairExecutionPolicy` adds a non-mutating preflight that binds the candidate, verified attempt, durable review reference, target, and exact approval arguments. The public `build_repair_candidate()` materializer rechecks the current Supervisor plan/ReviewPacket and validated manifest before delegating to that policy. A real D7 Free Worker artifact now materializes as a proposal-only `CANDIDATE`; no approval is consumed and no repair is executed. The deterministic temporary-Git regression still verifies approval consumption and the existing Host integration commit path without touching the official checkout or remote. Production approval storage, real repair execution, rollback execution, and official-branch integration remain unperformed. See [`d9-repair-candidate-policy-20260914.json`](../spec/v2/evidence/d9-repair-candidate-policy-20260914.json), [`d9-repair-approval-preflight-20260914.json`](../spec/v2/evidence/d9-repair-approval-preflight-20260914.json), [`d9-repair-host-integration-20260914.json`](../spec/v2/evidence/d9-repair-host-integration-20260914.json), and [`d9-repair-live-worker-candidate-20260914.json`](../spec/v2/evidence/d9-repair-live-worker-candidate-20260914.json).
- Numbered work coordination: `WorkAddress` provides a bounded mixed numeric/uppercase address projection while UUID task identity, dependency, ownership, and lease remain authoritative. `ResumeCapsule` now persists the active position, next action, owned paths, artifact references, and bounded LIFO interrupt stack; old capsules without the new stack remain readable. NOTE/PARALLEL/INTERRUPT/CANCEL classification is explicit and ambiguous input is non-interrupting. See [`coordination-work-egress-foundation-20260914.json`](../spec/v2/evidence/coordination-work-egress-foundation-20260914.json).
- Host egress boundary: a per-dispatch `EgressManifest` is built from an explicit standing low-risk grant, exact base-revision bytes, path/symlink/protected-path checks, bounded content scanning, sensitivity, size, UTF-8, and SHA-256. Only `ALLOW` reaches the existing Worker provider boundary; `REVIEW`/`DENY` remain Host outcomes and no secret/raw source content is recorded. The Worker prompt receives only a bounded transfer authorization projection.
- Process Coordination foundation: `ControlRequest` is durably stored and delivered through the existing mailbox, and `GuardianPolicy` can perform deterministic generation/lease/policy evaluation without any process creation, signalling, restart, or rollback side effect. This is preparation for a future Guardian, not process-control authority.
- CI regression follow-up: exact-head `a26c33c` passed `kernel (3.10)`, `kernel (3.11)`, and `provider-smoke` after the provider-saturation test fixture was made deterministic; the subsequent documentation checkpoints `39e08d9` and `ec0632d` also passed all three checks. The fixture change did not alter production dispatch or UNKNOWN/reconciliation semantics.

## Partially implemented / not verified

- Compression live availability is `NOT_VERIFIED`: the operator smoke reached the fixed client boundary but `COMPRESSION_API_TOKEN` was not present in the current environment, so no authenticated service result was claimed. See [`compression-service-connection-20260914.json`](../spec/v2/evidence/compression-service-connection-20260914.json). No token or raw response is stored.
- D4 same-operation resume is `NOT_AVAILABLE` for the concrete Codex backend without a formal external discovery API. The safe result is `UNKNOWN`/reconciliation, not inferred resume or blind restart.
- Process Coordination is foundation-only: peer/store/mailbox/immutable artifacts, work-position checkpoints, and read-only ControlRequest/Guardian fencing are locally verified. Guardian process operations, OS service integration, graceful drain, revision-pinned runtime, rolling restart, and rollback are not implemented or verified.
- D5 is a transport-neutral in-process/development adapter. A network MCP server/wire transport and Planner mutation authority are not implemented.
- D3 Worker reliability remains conditional. A bounded recurrence of malformed Python patches was classified as `patch_format_failure` / `host_verification_failure` / `model_output_invalid`; the Worker prompt now states syntax-completeness and delimiter-balance requirements, while the validator remains fail-closed. A separate outbound secret-candidate observation was rejected before sending. See [`d3-worker-reliability-observation-20260914.json`](../spec/v2/evidence/d3-worker-reliability-observation-20260914.json).
- Free L2 Reviewer shadow has two live proposal-only comparison samples and remains non-authoritative. The bounded D7 candidate cycle is live-evidence verified, but official-branch Codex-less integration is not enabled. D8 Host F0–F2 composition and the D9 deterministic candidate/approval-bound Host adapter are verified; D9 approval-bound integration is composition-tested in a temporary Git repository only, while live Worker-generated candidate materialization is now verified separately. Real repair execution, model-driven diagnosis, automatic improvement dispatch/repair, production approval storage, rollback execution, and official-branch promotion remain unconnected.

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

1. Keep D3 conditional and bounded; compare the recorded prompt-contract remediation against any future recurrence without weakening validation.
2. Preserve D4's explicit-discovery/UNKNOWN boundary; do not invent Codex session discovery.
3. Treat the D5 in-process adapter as the current boundary; wire transport and Planner mutation tools are separate future slices over the same authority.
4. Keep D7 official-branch Codex-less integration disabled. Treat D8/D9 as bounded Host boundaries; any real D9 execution must re-read all evidence and route an explicitly approved request through the existing Host integration/rollback authorities, with no automatic self-repair or blind retry.
5. Use the numbered work/resume and Host egress projections for future development delegation. The next coordination work is Guardian/drain/revision evidence; it must not add a second Task Scheduler or process authority.

The ordered roadmap is [`docs/V2_DETAILED_ROADMAP.md`](V2_DETAILED_ROADMAP.md),
and the compact operational procedure is [`docs/CODEX_DAILY_DOGFOOD.md`](CODEX_DAILY_DOGFOOD.md).

## Evidence index

- D4 Codex session discovery boundary: [`codex-session-restart-discovery-20260914.json`](../spec/v2/evidence/codex-session-restart-discovery-20260914.json)
- D5 MCP runtime adapter boundary: [`mcp-runtime-adapter-20260914.json`](../spec/v2/evidence/mcp-runtime-adapter-20260914.json)
- Compression client/live smoke boundary: [`compression-service-connection-20260914.json`](../spec/v2/evidence/compression-service-connection-20260914.json)
- D8 bounded Host F0–F2 composition: [`d8-self-improvement-composition-20260914.json`](../spec/v2/evidence/d8-self-improvement-composition-20260914.json)
- D9 bounded repair candidate policy: [`d9-repair-candidate-policy-20260914.json`](../spec/v2/evidence/d9-repair-candidate-policy-20260914.json)
- D9 approval-bound execution preflight: [`d9-repair-approval-preflight-20260914.json`](../spec/v2/evidence/d9-repair-approval-preflight-20260914.json)
- D9 temporary-Git Host integration composition: [`d9-repair-host-integration-20260914.json`](../spec/v2/evidence/d9-repair-host-integration-20260914.json)
- D9 live Worker candidate materialization: [`d9-repair-live-worker-candidate-20260914.json`](../spec/v2/evidence/d9-repair-live-worker-candidate-20260914.json)
- Coordination work/egress/Guardian foundation: [`coordination-work-egress-foundation-20260914.json`](../spec/v2/evidence/coordination-work-egress-foundation-20260914.json)
- Planner live D1 / D2 development evidence: [`planner-l2-live-d1-20260914.json`](../spec/v2/evidence/planner-l2-live-d1-20260914.json), [`planner-to-worker-e2e-20260914.json`](../spec/v2/evidence/planner-to-worker-e2e-20260914.json)
- Supplemental D2, D3, D6, and D7 observations: [`d2-dogfood-doc-note-20260914.json`](../spec/v2/evidence/d2-dogfood-doc-note-20260914.json), [`d3-worker-reliability-observation-20260914.json`](../spec/v2/evidence/d3-worker-reliability-observation-20260914.json), [`reviewer-shadow-20260914.json`](../spec/v2/evidence/reviewer-shadow-20260914.json), [`reviewer-shadow-20260914-02.json`](../spec/v2/evidence/reviewer-shadow-20260914-02.json), [`d7-codexless-candidate-20260914.json`](../spec/v2/evidence/d7-codexless-candidate-20260914.json)
- Model catalog and pool observations: [`spec/v2/evidence/`](../spec/v2/evidence/)
- Requirement traceability: [`spec/v2/TRACEABILITY.md`](../spec/v2/TRACEABILITY.md)
