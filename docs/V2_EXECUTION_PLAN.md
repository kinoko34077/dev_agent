# dev_agent v2 実行計画

この文書は現在の大きな順序だけを示すMain Roadmapである。詳細なGate、依存関係、完了条件は [`V2_DETAILED_ROADMAP.md`](V2_DETAILED_ROADMAP.md) を正本とする。要求は`docs/requirements/**`、decision rationaleは`spec/v2/adr/**`、観測証拠は`spec/v2/evidence/**`、Gate statusは`spec/v2/GATE_STATUS.json`を参照する。

## Current phase

Phase 7後半の安全な拡張と開発運用移管。既存のKernel、Resource/Provider、Task/Scheduler、Recovery、DevFarm、Supervisor、Host Verification、Review/Integration境界は維持する。G6O1は未検証の外部Gateだが、Human決定により現行roadmapでは非ブロッキング凍結中である。

進行表示は、歴史的なPhase 6 operational gateと混同しないよう、`spec/v2/GATE_STATUS.json`の`development_tracks`を併用する。現在は`D9_DOGFOOD=VERIFIED`、`D9_PRODUCTION_DEPLOYMENT=DEFERRED_NOT_READY`、`PHASE8_PREPARATION=PREPARATION_ONLY`である。Dogfoodは一件のbounded local trialに限るexit Gateであり、Production DeploymentのOS常駐・配備後recoveryを含まない。

## Implementation frontier

- Group Dのsession identity、bounded artifact reference、reconciliation replay、明示的Backend discovery authority。
- Free L1 WorkerのSupervisor/Host Verification/ReviewDecision/REWORK/依存integration経路。
- 2026-09-16のsource-only production Worker sliceでは、`src/dev_agent/resources/model_evidence_builder.py`、`src/dev_agent/providers/model_discovery.py`、`scripts/diagnose_model_candidates.py`をWorker起源で統合した。fresh Planner observationではqualified L2 poolを確認したが、3候補が`provider_unavailable`となり`pool_exhausted`で終了した。完全なlive Stage 5 multi-role activationは`NOT_VERIFIED`のまま。
- Free L2 Plannerのproposal-only adapter、strict JSON boundary、RootPlanningProposal、Host-only DevelopmentPlanningBridge、および明示的なModel Catalog / Benchmark Catalog / Capability Catalog / Runtime admission境界。
- Phase Aのredacted Host-process Planner probeではProvider応答まで到達したがstrict JSON decodeに失敗し、`model_output_invalid`としてboundedに分類できるよう`PlanningResponseError`とCLI projectionを追加した。`19b7770`では失敗時のfresh `parent_task_id`もprojectionへ残し、`62a9a81`では応答契約失敗をfresh `request_id`へ相関し、`63ecba2`ではblocked failureの生exception messageをprojectionから除外した。これは応答契約・相関・redaction診断の改善であり、Planner成功、AR1 recovery、Phase 8 live activationを意味しない。Evidenceは[`phase8-planner-redacted-model-output-20260917.json`](../spec/v2/evidence/phase8-planner-redacted-model-output-20260917.json)、[`planner-response-correlation-20260917.json`](../spec/v2/evidence/planner-response-correlation-20260917.json)、[`planner-shadow-bounded-error-projection-20260917.json`](../spec/v2/evidence/planner-shadow-bounded-error-projection-20260917.json)。
- `03e798a`ではProviderError transport failureとbounded pool-exhaustion projectionにもfresh UUID `request_id`相関を追加した。raw response/exception、retry、failover、reconciliation semantics、Planner/AR1/Phase 8 Gateは変更していない。Evidenceは[`planner-transport-request-correlation-20260917.json`](../spec/v2/evidence/planner-transport-request-correlation-20260917.json)。
- `73473ef`ではPlanner Shadowの予期しない例外fallbackからraw messageを除外し、型名と`reconciliation_required=true`だけをbounded projectionした。原因不明時に再送しないfail-closed診断であり、Provider routing・retry・Gateは変更していない。Evidenceは[`planner-shadow-unexpected-failure-redaction-20260917.json`](../spec/v2/evidence/planner-shadow-unexpected-failure-redaction-20260917.json)。
- Model-candidate diagnosis now reports static evidence candidates separately from final runtime eligibility (`static_eligible_count`, `runtime_unknown_count`, and `eligible_count`). This is a read-only diagnostic correction for Phase A route selection; it does not change billing, quota, health, or routing authority. Evidence is [`model-candidate-runtime-projection-20260917.json`](../spec/v2/evidence/model-candidate-runtime-projection-20260917.json).
- D6の異なる2件のproposal-only Reviewer Shadow比較、D7のbounded Codex-less candidate policy/CLI、D8 F0–F2のproposal-only data contracts。
- D8のHost composition: 既存Supervisorのcompact observationからF0 Observation、F1 Diagnosis、F2 Improvement Planをimmutableな`.devfarm/self-improvement/` artifactへ生成するread-only CLI。model-driven diagnosis、automatic repair、Task mutation、integrationは未接続。
- D9のbounded Controlled Self-Repair candidate policy、候補・verified attempt・durable review・対象・approval引数を束縛するread-only preflight、および既存Supervisor Host integrationへ接続する明示承認adapter。実在するproduction diagnostic issueについて、F0–F2 → Worker candidate → Host Verification → Codex review → durable exact approval → Host integration → pinned local health → known-good rollbackを一件完走した。Evidenceは[`d9-dogfood-real-repair-20260916.json`](../spec/v2/evidence/d9-dogfood-real-repair-20260916.json)。D9 Dogfoodはverifiedだが、OS配備Production DeploymentはDeferredのままである。
- Process Coordinationの基礎として、既存のCoordination store/mailbox/artifactへWork Address、Resume CapsuleのLIFO割込みstack、Host-owned Egress Manifest、generation-fenced ControlRequest、bounded Guardian action journal/evaluationを追加した。D10 G1〜G5では、静的Guardian process execution（G1は実ローカルsubprocessでも確認）、drain/checkpoint、revision-pinned runtime、rolling restart、last-known-good rollback compositionまでをlocal/fake runtimeで検証した。OS service、配備後crash recovery、Production runtime mutationは未接続であり、詳細と証拠は`V2_DETAILED_ROADMAP.md`、`spec/v2/evidence/guardian-fault-drill-20260915.json`、`spec/v2/evidence/guardian-real-local-process-20260915.json`を参照する。
- Local Operation runtime coordinatorとして、`RuntimeCoordinator`と固定CLIを追加した。これは既存`OperationService`のruntime準備・maintenance・durable Queue・WorkerRunner 1周期をpeer heartbeatで囲む薄いforeground process boundaryであり、第二Scheduler、Task state machine、Provider outbound path、retry authorityを持たない。local restart/idle/stale-generation fencingを検証済みだが、OS startupと配備後Guardian livenessはProduction DeploymentのDeferred trackに残る。Evidenceは[`operation-runtime-coordinator-local-20260917.json`](../spec/v2/evidence/operation-runtime-coordinator-local-20260917.json)。
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

G6O1-SIM/LIVE、real paid-provider qualification、OpenAI API、Claude API、OS-level sandbox evidence、Production auto-deploy、unbounded autonomous loop、Discord、Virtual Office UIはHumanの明示再開まで着手しない。G6O1は`DEFERRED_FROZEN` / `NOT VERIFIED` / `roadmap_blocking=false`として扱い、原要求を削除・昇格しない。Compression ServiceはHumanの明示指示で凍結解除されたが、固定profileのpayload最適化に限り、G6O1-SIM/LIVEの検証・billing authorityへ接続しない。

## Development rules

Free Workerに適した狭いTaskはWorker-first。Codexはdecomposition、authority-sensitive判断、review、integration、exception handlingを担当し、直接実装する場合は具体的理由をEvidenceへ残す。新しいScheduler、state machine、retry framework、Agent framework、MCP独自実行系は追加しない。

## References

- 現在状態: [`CURRENT_STATE.md`](CURRENT_STATE.md)
- 詳細順序: [`V2_DETAILED_ROADMAP.md`](V2_DETAILED_ROADMAP.md)
- 文書分類: [`DOCUMENTATION_INVENTORY.md`](DOCUMENTATION_INVENTORY.md)
- 運用契約: [`CODEX_COMMANDER.md`](CODEX_COMMANDER.md) / [`CODEX_SUPERVISOR.md`](CODEX_SUPERVISOR.md)
- 検証証拠: [`spec/v2/evidence/`](../spec/v2/evidence/)
