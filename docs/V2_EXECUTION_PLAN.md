# dev_agent v2 実行計画

この文書は現在の大きな順序だけを示すMain Roadmapである。詳細なGate、依存関係、完了条件は [`V2_DETAILED_ROADMAP.md`](V2_DETAILED_ROADMAP.md) を正本とする。要求は`docs/requirements/**`、decision rationaleは`spec/v2/adr/**`、観測証拠は`spec/v2/evidence/**`、Gate statusは`spec/v2/GATE_STATUS.json`を参照する。

2026-09-26 Issue #10 state/evidence hygiene: the accepted `v2/bootstrap`
baseline is now summarized at the top of `docs/CURRENT_STATE.md`; older
chronology remains traceable through Git history, Issues/PRs, and bounded
Evidence. `GATE_STATUS.json` blocker prose now reflects the later completed
local L1 root without changing any machine status or promoting Phase 8.

2026-09-26 Phase 8 failure-path lifecycle checkpoint: `542bdc2` extends the
existing bounded submission boundary so known local Planner/Worker/fallback/
Reviewer inventories are unloaded after both success and execution failure.
The original execution exception is preserved even when cleanup itself fails,
and malformed cleanup projection remains bounded diagnostic data;
no queue, scheduler, authority, retry, integration, or Gate semantics changed.
Focused affected coverage is `45 passed`; exact-head v2-core/provider-smoke
CI is green. A focused cleanup-boundary regression proves that an unexpected
cleanup exception cannot mask a completed result. This does not create a new live root and does not
change `PHASE8_LIVE_ACTIVATION=NOT_VERIFIED`. Evidence:
[`phase8-local-lifecycle-cleanup-20260926.json`](../spec/v2/evidence/phase8-local-lifecycle-cleanup-20260926.json).

2026-09-26 Phase 8 production composition checkpoint: `f47d382` now
composes one fresh Operation root through the existing Planner validation,
DevelopmentPlanningBridge, Commander/Supervisor, single-owner handoff,
Host Verification, separate Reviewer proposal/final decision, deterministic
integration, `CODE_INTEGRATED` continuation release, and one existing
RuntimeCoordinator cycle. The root is parked before continuation execution,
the root queue item is never claimed, the continuation reaches `COMPLETED`,
and the root closes terminally. The public driver is still limited to
submit/observe; this is one bounded cycle, not a scheduler. This is
deterministic local non-Gate evidence with fixture Provider responses;
remote generation admission, Discord live E2E, and `PHASE8 LIVE_ACTIVATION`
remain unchanged. Evidence:
[`phase8-production-composition-terminal-20260926.json`](../spec/v2/evidence/phase8-production-composition-terminal-20260926.json).

2026-09-26 fresh local L1 root checkpoint: `0529d12` uses the minimal
production-shaped Planner profile and a fresh qwen3.5:9b root. Both
non-overlapping Workers passed Host Verification and deterministic integration,
the existing review boundary was reached, `CODE_INTEGRATED` continuation
completed, and the root reached terminal completion with zero Codex direct
implementation. Gemma4 was available but unused; no remote route, third model,
UNKNOWN replay, or paid route was used. The trial did not independently
re-qualify a distinct Reviewer model, so this is
`OLLAMA_LOCAL_E2E=COMPLETED_NON_GATE`; `PHASE8_LIVE_ACTIVATION` remains
`NOT_VERIFIED`. Evidence:
[`phase8-local-e2e-20260926.json`](../spec/v2/evidence/phase8-local-e2e-20260926.json).

2026-09-26 read-only model evidence refresh: approved Host-boundary discovery
returned 1,357 candidate rows from 13 bindings, with one bounded Groq
`HTTPError`; the restricted comparison returned only 8 Ollama rows and
bounded `URLError` observations. The candidate was not merged into canonical
evidence. This is discovery evidence only; it does not establish runtime
admission or generation readiness. The post-merge exact local runtime snapshot
is recorded separately in Issue #15 evidence. No generation or UNKNOWN replay
was attempted. Evidence:
[`model-catalog-r9-refresh-20260926.json`](../spec/v2/evidence/model-catalog-r9-refresh-20260926.json).

2026-09-26 Issue #9 Worker contract/preflight checkpoint: `5fd89a1` adds a
Host-owned `minimal_file_replacement` proposal mode for local Ollama, retains
the legacy full-result mode, and records deterministic fail-closed preflight
metadata before any bounded correction or provider reassignment decision.
Only allowlisted transport formatting is canonicalized; source content, paths,
authority, Host Verification, UNKNOWN, and reconciliation semantics remain
unchanged. Affected DevFarm coverage is `194 passed`, full `tests/v2` is
`1676 passed, 1 skipped`, and exact-head v2-core/provider-smoke CI is green.
This is local non-Gate evidence; do not treat it as a new Phase 8 live root.
Evidence: [`devfarm-worker-contract-preflight-20260926.json`](../spec/v2/evidence/devfarm-worker-contract-preflight-20260926.json).

2026-09-27 Issue #15 admission synchronization: PR #16 implementation is
integrated at `81e47cea6500d9bdd8ad0cdd717f9e734d5e8a3a`; PR #17
documentation/evidence synchronization is at `59b2c7413db3f00a0fa6c8a08a08721bd3f83d22`.
Runtime admission is exact-identity and read-only. The current configured-pool
composition contains 8 resources but 0 quota domains; among the four exact
static candidates, only free-3/`gemini-3.5-flash-lite` reaches
`RUNTIME_UNKNOWN` (exact resource present, quota observation absent), while
the 3.6/3.8 identities are `RUNTIME_UNAVAILABLE` because their exact model
resources are not configured. No route is `RUNTIME_ELIGIBLE`; no generation
readiness is established. Discovery/model-list success is not runtime
admission or generation readiness. Do not start R9 from this snapshot; first
obtain an existing runtime-owned quota/health observation, then establish
separate fresh generation readiness for an exact `RUNTIME_ELIGIBLE` route. If
selecting a 3.8 identity, configure and qualify that exact binding separately.
Evidence:
[`model-runtime-admission-configured-pool-20260927.json`](../spec/v2/evidence/model-runtime-admission-configured-pool-20260927.json).
Formal Gate is unchanged.

## Current phase

Phase 7後半の安全な拡張と開発運用移管。既存のKernel、Resource/Provider、Task/Scheduler、Recovery、DevFarm、Supervisor、Host Verification、Review/Integration境界は維持する。G6O1は未検証の外部Gateだが、Human決定により現行roadmapでは非ブロッキング凍結中である。

進行表示は、歴史的なPhase 6 operational gateと混同しないよう、`spec/v2/GATE_STATUS.json`の`development_tracks`を併用する。現在は`D9_DOGFOOD=VERIFIED`、`D9_PRODUCTION_DEPLOYMENT=DEFERRED_NOT_READY`、`PHASE8_PREPARATION=PREPARATION_ONLY`である。Dogfoodは一件のbounded local trialに限るexit Gateであり、Production DeploymentのOS常駐・配備後recoveryを含まない。

2026-09-22 checkpoint: read-only model evidence refresh recovered 232 current catalog rows (32 static-eligible, 5 runtime-unknown in the static diagnostic). Four exact qualified/no-charge L2 identities entered a fresh bounded Host-process Planner pool; three distinct quota domains returned confirmed `provider_unavailable` and the pool was exhausted before a proposal. The separate Critic was not invoked, no root/child plan was created, and Phase 8 remains `PREPARATION_ONLY` / `LIVE_ACTIVATION=NOT_VERIFIED`. The prior UNKNOWN request was not replayed. Evidence: [`model-evidence-refresh-r9-planner-blocker-20260922.json`](../spec/v2/evidence/model-evidence-refresh-r9-planner-blocker-20260922.json). Resume R9 only on a new eligible route and fresh request identity; natural FORMAT/PATCH or SEMANTIC/TEST failure may use AR1, but AR1 is not a precondition to R9.

2026-09-22 local fallback checkpoint: the explicitly authorized `qwen3.5:9b` Ollama binding now has a bounded HTTP lifecycle manager, `keep_alive` propagation, explicit timeout propagation into Host child configuration, and local Worker/Reviewer opt-in plumbing. Real cold/warm inference and unload were observed. The local E2E reached Worker output, Host Verification, proposal-only Reviewer Shadow, and bounded rework, but no patch reached deterministic integration; record this as `OLLAMA_LOCAL_E2E=PARTIAL_BLOCKED` and keep the formal Phase 8 Gate unchanged. Evidence: [`ollama-local-lifecycle-e2e-20260922.json`](../spec/v2/evidence/ollama-local-lifecycle-e2e-20260922.json). Return to the R9 fresh-root route when a newly observed qualified remote L2 route is available; do not replay the prior UNKNOWN operation.

2026-09-22 local Planner convergence checkpoint: a fresh local `qwen3.5:9b` Planner response-contract failure was corrected once by a distinct `qwen3:8b` proposal-only Critic and passed Host planning validation. A separate fresh request reached the bounded same-signature stop. This records real local bounded convergence and the explicit auxiliary Critic binding, but does not claim Worker integration, AR1 completion, or Phase 8 LIVE_ACTIVATION. Evidence: [`ollama-local-planner-convergence-20260922.json`](../spec/v2/evidence/ollama-local-planner-convergence-20260922.json).

2026-09-22 local Worker convergence checkpoint: the local Worker now sends an Ollama-only bounded JSON/file-replacement request while remote Worker contracts remain unchanged. A fresh two-task root reached qwen3.5 output-contract failures, then one bounded same-tier qwen3:8b reassignment; the alternate path produced a bounded HTTP 400 transport failure and a sibling egress rejection. No patch reached Host Verification or integration, so AR1/local E2E remains `PARTIAL_BLOCKED`, and the formal Phase 8 Gate is unchanged. Evidence: [`ollama-local-worker-convergence-20260922.json`](../spec/v2/evidence/ollama-local-worker-convergence-20260922.json).

2026-09-23 concrete local convergence checkpoint: `qwen3.5:9b` remains the primary L1 local-trial model and `gemma4:12b` the only fallback; `qwen3.5:4b` is not in automatic routing. Host-derived `ConcreteFailureSpec` / `RepairDirective` now covers invalid JSON-object, scalar/array, replacement-line, path-scope, and embedded-newline findings, with correction state passed through fresh rework manifests. The latest follow-up rebound the newest Qwen directive into a fresh Gemma attempt that passed independent Host Verification for Worker A; the separate Worker B request reached `read_timeout` at `response_wait` and was closed as reconciliation-required without replay. Reviewer/integration/dependent continuation were not reached. Evidence: [`ollama-local-worker-rebind-followup-20260923.json`](../spec/v2/evidence/ollama-local-worker-rebind-followup-20260923.json). Formal Phase 8 remains `PREPARATION_ONLY` / `LIVE_ACTIVATION=NOT_VERIFIED`.
2026-09-23 local Host-timeout/convergence checkpoint: `HostProcessExecutor` now forwards its existing bounded timeout into the static child Ollama binding, eliminating the prior child-side 30-second fallback after model load. A fresh qwen9 Worker trial produced deterministic output-contract failures; task A reached independent Host Verification through Gemma fallback, while task B failed Host Verification after two concrete Gemma corrections. No Reviewer, integration, dependent continuation, third model, or UNKNOWN replay occurred. Evidence: [`ollama-local-timeout-convergence-20260923.json`](../spec/v2/evidence/ollama-local-timeout-convergence-20260923.json). Formal Phase 8 remains `PREPARATION_ONLY` / `LIVE_ACTIVATION=NOT_VERIFIED`.

2026-09-23 R10-PREP tiny-root continuation: a fresh qwen3.5:9b Planner proposal passed Host validation and created two non-overlapping child concepts. The first tiny Worker task made one measurable validation-rung advance (`invalid_json` to embedded-newline), then repeated the prior invalid-JSON signature; one fresh gemma4:12b fallback failed with extra JSON data. No Worker patch reached Host Verification or integration, so this is weak-model convergence preparation evidence only, not formal R10. Evidence: [`ollama-local-r10-tiny-worker-20260923.json`](../spec/v2/evidence/ollama-local-r10-tiny-worker-20260923.json).
2026-09-23 R10-PREP fresh-root rebind trial: a new qwen3.5:9b Planner proposal passed Host validation. Two non-overlapping local Worker scopes were then attempted with qwen3.5:9b primary and gemma4:12b fallback. Worker A progressed through output-contract checks but failed Host syntax verification; Worker B reached Host verification after Gemma fallback but failed syntax verification. Latest RepairDirective rebinding and recurrence handling were exercised, with no third model, UNKNOWN replay, Reviewer, integration, or dependent continuation. `OLLAMA_LOCAL_E2E=PARTIAL_BLOCKED`; formal Phase 8 remains unchanged. Evidence: [`ollama-local-fresh-root-rebind-20260923.json`](../spec/v2/evidence/ollama-local-fresh-root-rebind-20260923.json).

2026-09-23 operational runtime checkpoint: the existing `RuntimeCoordinator` now has a durable `WAITING_HUMAN` continuation path through the existing SQLite StateStore, while unrelated READY work continues. The bounded Codex JSON-lines adapters separate `HUMAN_REQUIRED` Human Proxy responses from `PROPOSAL_ONLY` Expert Assist proposals; a Codex response is never converted into Human approval. Existing pinned-release/rolling/rollback primitives are composed by `SelfUpdateService` with trusted-ref and preflight checks, durable failed-candidate suppression, and LKG rollback. `start-dev-agent.bat` is a thin foreground launcher only. Evidence: [`operational-human-assist-self-update-local-20260923.json`](../spec/v2/evidence/operational-human-assist-self-update-local-20260923.json). This is local operational evidence and does not change D9 Production Deployment or Phase 8 Gate values; external Codex MCP E2E and OS startup remain deferred.

2026-09-23 Discord Human UI checkpoint: the optional thin adapter now has local configuration/echo/command/rendering tests, durable SQLite channel/thread pointers, restart-safe message idempotency/delivery metadata, exact HumanInteractionPort response correlation, proposal-only Approval delegation, and callback-only projection into existing Operation/Process Coordination boundaries. The ignored local `.env` contains the operator configuration and Bot Token only; Host Gateway smoke reached Discord login, but the Portal setting was not independently reverified in the repository and no interactive client was available for a new message. Server installation, live message/button handling, and live Gateway E2E remain pending. This slice does not alter D9/Phase 8 Gates. Evidence: [`discord-human-ui-local-20260923.json`](../spec/v2/evidence/discord-human-ui-local-20260923.json).

2026-09-23 Discord standard-runner composition checkpoint: `336be54` makes `run_from_environment()` open one existing SQLite-backed `DiscordRuntimeComposition`, inject its durable binding store, shared numeric authorizer, Operation submit/status boundary, HumanInteractionPort adapter, proposal-only Approval factory, and Process Coordination mailbox projection into `build_bot`. The runner does not start a second scheduler; `RuntimeCoordinator` remains the execution owner. Unauthorized, bot, malformed, and duplicate messages are silent, and accepted read queries render bounded status projections. Focused Discord tests, full regression, architecture, compileall, and exact-head CI are green for `336be54`. Evidence: [`discord-core-runner-composition-20260923.json`](../spec/v2/evidence/discord-core-runner-composition-20260923.json).

2026-09-23 Discord outbound projection checkpoint: the standard runner starts one read-only `DiscordOutboundPublisher` after Gateway readiness. It projects pending HumanRequest records and the latest bounded task/event progress to existing channel/thread bindings, using existing durable delivery metadata for idempotency. The loop survives transient observation/send exceptions without becoming a scheduler or authority owner. Approval-button auto-delivery remains opt-in to an explicit Core-owned approval view/submit boundary and is not claimed for the standard runner. Live ordinary-message, progress, HumanRequest, and button E2E still require a real Discord client and remain unverified. Evidence: [`discord-outbound-projection-20260923.json`](../spec/v2/evidence/discord-outbound-projection-20260923.json).

2026-09-23 Discord operational routing checkpoint: `639d903` connects active-run plain-message routing to the existing NOTE boundary, preserves explicit intervention kinds, uses Core child submission for bound PARALLEL work, cancels only the bound Task without requesting a process-wide stop, and persists `/dir`/`/file` UI scope in StateStore schema version 9 for structured Core inputs. Discord replies to delivered HumanRequests are correlated before ordinary ingress and finite-answer buttons delegate to `HumanInteractionPort`; outbound projection exposes a bounded health/error category. The Core safe-checkpoint consumer for INTERRUPT and the standard-runner Core-owned Approval submit callback remain unexposed, so those two operational paths are not claimed as complete. Local tests and exact-head CI pass; real Discord Human-message/button E2E remains pending interactive-client observation. Evidence: [`discord-operational-routing-20260923.json`](../spec/v2/evidence/discord-operational-routing-20260923.json).

The operational slice is a bounded continuation of the existing roadmap, not a new phase: after the Issue #5/PR #4 composition proof and Issue #9 Worker contract/preflight slice, resume R9 fresh-root live execution only on a newly admitted remote generation route. If no such route exists, do not repeat the prior failed route; use only a new local hypothesis. Use AR1 only for a naturally observed eligible Worker failure, and keep Stage 6/7/8 work behind the existing Phase 8 activation conditions. Discord remains a non-Gate surface with live-client observations still explicitly pending.

## Implementation frontier

- Group Dのsession identity、bounded artifact reference、reconciliation replay、明示的Backend discovery authority。
- Free L1 WorkerのSupervisor/Host Verification/ReviewDecision/REWORK/依存integration経路。
- 2026-09-16のsource-only production Worker sliceでは、`src/dev_agent/resources/model_evidence_builder.py`、`src/dev_agent/providers/model_discovery.py`、`scripts/diagnose_model_candidates.py`をWorker起源で統合した。fresh Planner observationではqualified L2 poolを確認したが、3候補が`provider_unavailable`となり`pool_exhausted`で終了した。完全なlive Stage 5 multi-role activationは`NOT_VERIFIED`のまま。
- Free L2 Plannerのproposal-only adapter、strict JSON boundary、RootPlanningProposal、Host-only DevelopmentPlanningBridge、および明示的なModel Catalog / Benchmark Catalog / Capability Catalog / Runtime admission境界。
- Phase Aのredacted Host-process Planner probeではProvider応答まで到達したがstrict JSON decodeに失敗し、`model_output_invalid`としてboundedに分類できるよう`PlanningResponseError`とCLI projectionを追加した。`19b7770`では失敗時のfresh `parent_task_id`もprojectionへ残し、`62a9a81`では応答契約失敗をfresh `request_id`へ相関し、`63ecba2`ではblocked failureの生exception messageをprojectionから除外した。これは応答契約・相関・redaction診断の改善であり、Planner成功、AR1 recovery、Phase 8 live activationを意味しない。Evidenceは[`phase8-planner-redacted-model-output-20260917.json`](../spec/v2/evidence/phase8-planner-redacted-model-output-20260917.json)、[`planner-response-correlation-20260917.json`](../spec/v2/evidence/planner-response-correlation-20260917.json)、[`planner-shadow-bounded-error-projection-20260917.json`](../spec/v2/evidence/planner-shadow-bounded-error-projection-20260917.json)。
- `03e798a`ではProviderError transport failureとbounded pool-exhaustion projectionにもfresh UUID `request_id`相関を追加した。raw response/exception、retry、failover、reconciliation semantics、Planner/AR1/Phase 8 Gateは変更していない。Evidenceは[`planner-transport-request-correlation-20260917.json`](../spec/v2/evidence/planner-transport-request-correlation-20260917.json)。
- `73473ef`ではPlanner Shadowの予期しない例外fallbackからraw messageを除外し、型名と`reconciliation_required=true`だけをbounded projectionした。原因不明時に再送しないfail-closed診断であり、Provider routing・retry・Gateは変更していない。Evidenceは[`planner-shadow-unexpected-failure-redaction-20260917.json`](../spec/v2/evidence/planner-shadow-unexpected-failure-redaction-20260917.json)。
- `e66f3e3`では`invalid_json` / `invalid_proposal`をPlanner adapterの構造化属性として投影し、例外文言からの分類推測を除去した。request相関、raw detail redaction、retry/failover/UNKNOWN semantics、Gateは変更していない。Evidenceは[`planner-response-contract-structured-classification-20260917.json`](../spec/v2/evidence/planner-response-contract-structured-classification-20260917.json)。
- `7b736b8`では、Provider応答が観測済みの`invalid_json` / `invalid_proposal`に限り、明示的に別admissionされたL1 planning Criticへ一回だけ補正を委譲し、修正版を既存`RootPlanningValidator`へ戻すbounded compositionをPlanner Shadow CLIへ接続した。成功したPlannerはCriticを呼ばず、transport/quota/security/UNKNOWNはCriticへ流さない。Planner/Criticの同一provider/binding/model identityは拒否し、CLIのCritic pool指定も非secret binding metadataに限定する。これは契約・ローカル検証であり、live Planner recovery、AR1、Phase 8 LIVE_ACTIVATIONを意味しない。Evidenceは[`planner-independent-critic-contract-20260917.json`](../spec/v2/evidence/planner-independent-critic-contract-20260917.json)。
- `7000102`では、独立Planner CriticのProvider failureおよびresponse-contract failureにCritic自身のfresh `request_id`を相関し、Planner Shadowのbounded projectionへ検証済みUUIDだけを投影した。raw response/exception message、retry、failover、UNKNOWN replay、Gateは変更していない。これは診断強化のローカル検証であり、live Planner recovery、AR1、Phase 8 LIVE_ACTIVATIONを意味しない。Evidenceは[`planner-critic-failure-correlation-20260917.json`](../spec/v2/evidence/planner-critic-failure-correlation-20260917.json)。
- `fafd41b`では、Planner Criticのadapter/schema/error/composition helperを既存のlazy `src.dev_agent.intelligence` package boundaryへ公開した。eager pipeline import、Provider request、authority、retry、UNKNOWN、Gateは変更していない。Evidenceは[`planner-critic-public-boundary-20260917.json`](../spec/v2/evidence/planner-critic-public-boundary-20260917.json)。
- `5a778d7`では、Planner Shadow CLIの`PlanningCriticAdapterError`投影で例外束縛漏れを修正し、Criticのbounded response-contract failureを安全な`model_output_invalid` JSONへ相関付きで投影できるようにした。raw response/exception、replay、reconciliation、authority、Gateは変更していない。Evidenceは[`planner-critic-cli-failure-projection-20260917.json`](../spec/v2/evidence/planner-critic-cli-failure-projection-20260917.json)。
- fresh Host-process Planner probeでは、qualified L2 Plannerと別admissionのCloudflare L1 Criticを構成し、UNKNOWN-quota admission後にProvider境界へ到達したが`provider_decode`/`reconciliation_required=true`で停止した。response-contract failureではないためCriticは呼ばれず、既存のno-replay semanticsを維持した。Evidenceは[`planner-critic-live-probe-20260917.json`](../spec/v2/evidence/planner-critic-live-probe-20260917.json)。
- `e84ed8d`では、Planner Shadowの成功projectionへ、別admissionされたproposal-only Criticの設定有無・実呼出し有無・fresh request UUID・bounded dispatch auditを追加した。successful PlannerはCriticを呼ばず、Critic adapterは各correction前に古いUUIDを破棄する。これはPlanner cross-checkの観測性であり、live Planner correction、AR1、Phase 8 LIVE_ACTIVATION、retry/failover/UNKNOWN authorityの変更ではない。Evidenceは[`planner-critic-observation-projection-20260917.json`](../spec/v2/evidence/planner-critic-observation-projection-20260917.json)。
- その後、freshな`gemini:worker:free-4` / `free-5` L2 Planner requestを一件ずつ実行したが、いずれも`provider_unavailable`でresponse未観測のまま終了した。別admissionのCriticはresponse-contract failureでないため呼ばれていない。これはconfirmed failover-safe availability blockerであり、同一binding再送、prior UNKNOWN replay、L1 downgrade、Gate変更は行っていない。Evidenceは[`phase8-planner-alternate-provider-unavailable-20260917.json`](../spec/v2/evidence/phase8-planner-alternate-provider-unavailable-20260917.json)。
- R9のfresh Planner試行では、明示的にqualified L2へadmissionし、独立Cloudflare L1 Criticを分離した後も、`gemini:worker:free-3`の`gemini-3.6-flash`と一軸変更した`gemini-3.8-flash`がともに`provider_unavailable`で応答前に終了した。これはconfirmed external availability blockerであり、Critic補正、Host proposal validation、AR1、Phase 8 LIVE_ACTIVATIONへ進める結果ではない。過去UNKNOWNのreplay、同一binding/modelのblind retry、L1 downgrade、Gate変更は行っていない。Evidenceは[`phase8-planner-fresh-provider-unavailable-20260917.json`](../spec/v2/evidence/phase8-planner-fresh-provider-unavailable-20260917.json)。
- `5cb658a`では、Planner Shadowの失敗projectionにも、別admissionされたproposal-only Criticの設定有無・呼出し有無・呼出し時のfresh UUID・bounded dispatch auditを安全に保持した。raw Provider/exception detailは除外し、transport/quota/security/UNKNOWNはCritic補正へ流さず、retry/failover/replay/authority/Gateも変更していない。これはPlanner cross-checkの失敗観測性であり、live Planner correction、AR1、Phase 8 LIVE_ACTIVATIONを意味しない。Evidenceは[`planner-critic-failure-observation-20260917.json`](../spec/v2/evidence/planner-critic-failure-observation-20260917.json)。
- `03491b9`では、`run_shadow`の実Provider failure経路について、Critic未起動・reconciliation維持・限定されたCritic観測projectionを回帰テストで固定した。これはPlanner cross-checkのfailure-path coverageであり、live Planner correction、AR1、Phase 8 LIVE_ACTIVATION、retry/failover/replay、Gate遷移を意味しない。Evidenceは[`planner-critic-failure-observation-20260917.json`](../spec/v2/evidence/planner-critic-failure-observation-20260917.json)。
- Model-candidate diagnosis now reports static evidence candidates separately from final runtime eligibility (`static_eligible_count`, `runtime_unknown_count`, and `eligible_count`). This is a read-only diagnostic correction for Phase A route selection; it does not change billing, quota, health, or routing authority. Evidence is [`model-candidate-runtime-projection-20260917.json`](../spec/v2/evidence/model-candidate-runtime-projection-20260917.json).
- D6の異なる2件のproposal-only Reviewer Shadow比較、D7のbounded Codex-less candidate policy/CLI、D8 F0–F2のproposal-only data contracts。
- D8のHost composition: 既存Supervisorのcompact observationからF0 Observation、F1 Diagnosis、F2 Improvement Planをimmutableな`.devfarm/self-improvement/` artifactへ生成するread-only CLI。model-driven diagnosis、automatic repair、Task mutation、integrationは未接続。
- D9のbounded Controlled Self-Repair candidate policy、候補・verified attempt・durable review・対象・approval引数を束縛するread-only preflight、および既存Supervisor Host integrationへ接続する明示承認adapter。実在するproduction diagnostic issueについて、F0–F2 → Worker candidate → Host Verification → Codex review → durable exact approval → Host integration → pinned local health → known-good rollbackを一件完走した。Evidenceは[`d9-dogfood-real-repair-20260916.json`](../spec/v2/evidence/d9-dogfood-real-repair-20260916.json)。D9 Dogfoodはverifiedだが、OS配備Production DeploymentはDeferredのままである。
- Process Coordinationの基礎として、既存のCoordination store/mailbox/artifactへWork Address、Resume CapsuleのLIFO割込みstack、Host-owned Egress Manifest、generation-fenced ControlRequest、bounded Guardian action journal/evaluationを追加した。D10 G1〜G5では、静的Guardian process execution（G1は実ローカルsubprocessでも確認）、drain/checkpoint、revision-pinned runtime、rolling restart、last-known-good rollback compositionまでをlocal/fake runtimeで検証した。OS service、配備後crash recovery、Production runtime mutationは未接続であり、詳細と証拠は`V2_DETAILED_ROADMAP.md`、`spec/v2/evidence/guardian-fault-drill-20260915.json`、`spec/v2/evidence/guardian-real-local-process-20260915.json`を参照する。
- Local Operation runtime coordinatorとして、`RuntimeCoordinator`と固定CLIを追加した。これは既存`OperationService`のruntime準備・maintenance・durable Queue・WorkerRunner 1周期をpeer heartbeatで囲む薄いforeground process boundaryであり、第二Scheduler、Task state machine、Provider outbound path、retry authorityを持たない。local restart/idle/stale-generation fencingを検証済みだが、OS startupと配備後Guardian livenessはProduction DeploymentのDeferred trackに残る。Evidenceは[`operation-runtime-coordinator-local-20260917.json`](../spec/v2/evidence/operation-runtime-coordinator-local-20260917.json)。
- `f0a2468`では、bounded convergence R7/R8を既存Operation境界へ薄く接続した。Operation statusは検証済み`ConvergenceMetadata`だけを投影し、reconciliation待ちTaskをdurableに保持したまま無関係なREADY workを継続できる。Phase 8相当のPlanner→Implementer A/B→`CODE_INTEGRATED` continuationは既存Operation/RuntimeCoordinatorへ再適用でき、restart後もtask/attempt重複を作らない。これはlocal deterministic evidenceであり、live AR1・fresh live Phase 8 root・LIVE_ACTIVATIONの証拠ではない。Evidenceは[`bounded-convergence-runtime-r7-r8-20260917.json`](../spec/v2/evidence/bounded-convergence-runtime-r7-r8-20260917.json)。
- `2cb6639`では、Planner ShadowのProvider呼出し前のroute/admission/resource-pool組成失敗を、raw exception detailなしの`blocked_local` / `reconciliation_required=false`へ分離した。Provider transport、failover、quota、UNKNOWN replay、Gateは変更していない。Evidenceは[`planner-shadow-local-admission-diagnostics-20260917.json`](../spec/v2/evidence/planner-shadow-local-admission-diagnostics-20260917.json)。
- `416ce96`では、既存R3のbounded Planner convergenceをPlanner Shadow CLIへ接続した。独立admission済みL1 Critic poolを指定した場合、valid responseは`FAST_PATH`としてCritic追加呼出しなしでHost `RootPlanningValidator`へ進み、`invalid_json` / `invalid_proposal`だけがfresh requestのproposal-only Critic補正へ入る。補正結果も同じHost validatorへ戻し、bounded attempts/refinement roundsを投影する。transport/quota/security/UNKNOWN、retry/failover、Gateは変更していない。Evidenceは[`planner-shadow-bounded-convergence-20260917.json`](../spec/v2/evidence/planner-shadow-bounded-convergence-20260917.json)。
- 2026-09-17に実稼働中のlocal foreground Coordinatorで、idle中に投入したdurable fake Taskを次周期で1 claim/1 attempt完了し、durable stop後に次generationへ再起動して既存Task状態を保持することを確認した。これはG-3 idle→work / G-4 restartのlocal process evidenceであり、OS startup、配備後Guardian recovery、AR1、Phase 8 LIVE_ACTIVATIONを意味しない。Evidenceは[`operation-runtime-live-idle-restart-20260917.json`](../spec/v2/evidence/operation-runtime-live-idle-restart-20260917.json)。
- 開発運用のNetwork/Authority境界として、標準Worker/Planner/CriticをHost-owned one-shot dispatchへ接続し、WinError 10013等のtransport分類、per-dispatch Egress Manifest、read-only binding/digest preflight、terminal Plan supersession、六秒既定のGuardian serve、静的OS登録dry-runを実装した。Free-3によるproduction Workerの複数実装・Host Verification・Codex review・Host integrationは[`phase8-live-production-worker-integration-20260916.json`](../spec/v2/evidence/phase8-live-production-worker-integration-20260916.json)で検証済みである。これはproduction Worker subgateの完了であり、同一rootのlive Stage 5 multi-role activationではない。OS登録・配備後crash recovery・D9 official runtime mutationは未検証であり、`spec/v2/evidence/network-authority-guardian-20260915.json`を参照する。
- Group DのCodex session restart/discovery境界は、正式な外部discoveryが無い場合にUNKNOWNへ閉じることをEvidence化した。MCPは既存Supervisorへ委譲するtransport-neutralなin-process thin adapterまで接続し、wire transportは未接続。Compressionは固定HTTP clientを明示compositionでき、Credential Manager経由のbounded live smokeはendpointからHTTP 403を受けたため、認証済み圧縮結果は未検証。

## Current Gate

### D0 — Documentation SSOT consolidation

Current State、Main/Detailed Roadmap、Requirements、ADR、Evidence、作業Planの役割を分離し、過去の完了Planをarchiveした。現在の詳細正本は`V2_DETAILED_ROADMAP.md`である。

### D1/D2 — Planner-to-Worker live development slice

D1/D2のbounded live development sliceは、`gemini:worker:free-3` / `gemini-3.6-flash`によるstrict JSON Planner proposal、Host validation、DevelopmentPlanningBridge、Commander Plan、Free L1 Worker、独立Host Verification、Codex durable review、Host deterministic integrationまで完了した。さらに、`gemini:worker:free-3` / `gemini-3.5-flash-lite`によるproduction `scripts/` Worker taskと、別のproduction `src/` Worker task 2件も同じHost境界で完走し、production Worker subgateを検証済みとした。完全な同一root live Stage 5はまだ未検証である。詳細Evidenceは[`planner-l2-live-d1-20260914.json`](../spec/v2/evidence/planner-l2-live-d1-20260914.json)、[`planner-to-worker-e2e-20260914.json`](../spec/v2/evidence/planner-to-worker-e2e-20260914.json)、[`d2-production-worker-integration-20260916.json`](../spec/v2/evidence/d2-production-worker-integration-20260916.json)、[`phase8-live-production-worker-integration-20260916.json`](../spec/v2/evidence/phase8-live-production-worker-integration-20260916.json)を参照する。Model discovery / benchmark / capability evidenceは候補化の入力に留まり、qualification・billing・privacy・quota・healthを代替しない。confirmed failover-safe failureは同一tierの別bindingへ切替え、UNKNOWNはreconciliationへ閉じる。

D4のdiscovery境界確認とD5のtransport-neutral in-process thin adapterは完了した。D4は正式なdiscovery authorityが無い限りUNKNOWN/reconciliationを維持し、D5のwire transportとPlanner mutation authorityは未接続の別sliceである。D2の別内容の補助Worker sliceと、D6の異なる2件のproposal-only Reviewer Shadow比較もEvidence化した。D7のbounded candidate policy/CLIは、実Free L1 Worker、独立Host Verification、Free L2 proposal-only review、Host candidate評価まで一続きのlive evidenceを取得した。D8は既存Supervisorからbounded F0–F2 artifactを生成するHost composition、D9はdeterministic repair-candidate gating、read-only approval preflight、既存Supervisor Host integrationへ接続する明示承認adapterまで検証済みであり、一時Git repositoryでapproval-bound Host integrationの決定的compositionを検証し、実D7 Worker artifactのproposal-only candidate materializationも別Evidence化した。D10 G1〜G5ではGuardianのlocal execution/drain/pinned release/rolling/rollback compositionとfault drillまで検証した。さらにD9 Dogfoodでは、実在するlow-risk production issueについてF0–F2、Free L1 Worker、独立Host Verification、Codex review、durable exact approval consume-once、Host integration、pinned local runtime health、known-good rollbackを一件完走し、`D9_DOGFOOD=VERIFIED`となった。Production Deployment側はTask Scheduler/OS常駐・配備後recoveryを保留する。Human承認は候補へ束縛された一件の実行を許可するもので、無制限self-repairやCodex-less official automationを意味しない。次はAR1の一回限定live recoveryと、D9 Dogfood依存を満たしたPhase 8 fresh-root live multi-roleへ進み、Phase 8 live activationには引き続き独立したPlanner→Implementer→Reviewer→dependent continuation→Host integration Evidenceを要求する。詳細は[`V2_DETAILED_ROADMAP.md`](V2_DETAILED_ROADMAP.md)と[`02-dogfood-and-production-gates.md`](requirements/process-coordination/02-dogfood-and-production-gates.md)を正本とする。

## Operational acceptance

Code、local regression、Host Verification、Git integration、remote push、exact-head CIは別Evidenceとして扱う。Provider request到達、adapter存在、MCP boundary、モデル自己申告だけではGateを閉じない。詳細順序はD3 Worker reliability、D4 concrete session restart/discovery、D5 MCP thin runtime、D6 Reviewer Shadow、D7 LOW/NORMAL Codex-less cycle、D8–D9 Self-Improvement、D10 Guardian operational foundationへ続く。D9 Dogfoodは実在issueの一件限定local repair/health/rollbackまでverifiedだが、Production DeploymentはOS常駐・配備後recoveryを含まずDeferredである。Phase 8 live activationには、引き続き`D9_DOGFOOD=VERIFIED`と独立したlive multi-role evidenceを要求する。

## External / frozen

G6O1-SIM/LIVE、real paid-provider qualification、OpenAI API、Claude API、OS-level sandbox evidence、Production auto-deploy、unbounded autonomous loop、Discord live Gateway/Portal operation、Virtual Office UIは正式Gate候補へ進めない。Discordの薄いlocal UI adapterだけはHuman指示で再開した非Gate sliceであり、Bot Token/Portal setupとlive E2Eは未完了のまま保持する。G6O1は`DEFERRED_FROZEN` / `NOT VERIFIED` / `roadmap_blocking=false`として扱い、原要求を削除・昇格しない。Compression ServiceはHumanの明示指示で凍結解除されたが、固定profileのpayload最適化に限り、G6O1-SIM/LIVEの検証・billing authorityへ接続しない。

## Development rules

Free Workerに適した狭いTaskはWorker-first。Codexはdecomposition、authority-sensitive判断、review、integration、exception handlingを担当し、直接実装する場合は具体的理由をEvidenceへ残す。新しいScheduler、state machine、retry framework、Agent framework、MCP独自実行系は追加しない。

## References

- 現在状態: [`CURRENT_STATE.md`](CURRENT_STATE.md)
- 詳細順序: [`V2_DETAILED_ROADMAP.md`](V2_DETAILED_ROADMAP.md)
- 文書分類: [`DOCUMENTATION_INVENTORY.md`](DOCUMENTATION_INVENTORY.md)
- 運用契約: [`CODEX_COMMANDER.md`](CODEX_COMMANDER.md) / [`CODEX_SUPERVISOR.md`](CODEX_SUPERVISOR.md)
- 検証証拠: [`spec/v2/evidence/`](../spec/v2/evidence/)
