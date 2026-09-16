# dev_agent v2 詳細ロードマップ

この文書は、[V2_EXECUTION_PLAN.md](V2_EXECUTION_PLAN.md) が示す現在の大きな順序を、実装可能なGateと依存関係へ展開する詳細正本である。要求・ADR・Evidenceを複製せず、各Gateの完了条件と参照先だけを持つ。

## 現在位置

`D0 Documentation SSOT consolidation`、`D1 Valid live Free L2 proposal`、`D2 Planner → Bridge → Commander → L1 Worker E2E` はbounded live evidenceで完了した。D2には、2026-09-16に`gemini:worker:free-3` / `gemini-3.5-flash-lite`を使ったproduction `scripts/` Worker taskの実装、独立Host Verification、Codex review、Host deterministic integrationまでが追加された。さらに別のlive Gemini dispatchで`src/dev_agent/resources/model_candidates.py`と`src/dev_agent/providers/normalize.py`のproduction Worker統合を完了し、production Worker subgateを検証した。ただしこれは完全なStage 5 live multi-role activationではない。D4はCodexExecBackendに正式な外部discoveryがないため、明示DiscoveryAuthority以外の復元を行わずUNKNOWNへ閉じる境界を確認した。D5のtransport-neutral thin runtime adapterまで進め、wire transportは未接続である。D6は異なる2件のproposal-only Reviewer Shadow比較を取得し、D7の決定的candidate policy/CLIと、D8 F0–F2のproposal-onlyデータ契約を実装した。さらにD7の実Free Worker → Host Verification → Free L2 proposal-only → Host candidate評価を証拠化した。D10のG1〜G5として、静的Guardian process実行、graceful drain/checkpoint、revision-pinned release、rolling restart、last-known-good rollback compositionとfault drillをHost/localで検証した。直近では標準Worker/Planner/Critic dispatchをHost-owned one-shot boundaryへ寄せ、transport/egress証跡、明示Plan supersede、六秒既定のGuardian serve、OS登録dry-runを追加した。DevFarmは、plan query、assigned Provider composition、Worker prompt、Worker output/metrics projectionの限定責務分離まで進めた。ただし公式branchへのCodex-less統合、OS常駐Guardian、配備後crash recovery、D9のautomatic/official runtime mutationは未接続である。一件限定Human-approved D9 Dogfood real mutationはverifiedである。新しいlive production Worker evidenceは[`phase8-live-production-worker-integration-20260916.json`](../spec/v2/evidence/phase8-live-production-worker-integration-20260916.json)を参照する。
2026-09-16追加: source-only production Worker sliceで`src/dev_agent/resources/model_evidence_builder.py`、`src/dev_agent/providers/model_discovery.py`、`scripts/diagnose_model_candidates.py`の3件を統合し、1件のcontract-shape failureをbounded reworkで閉じた。fresh Planner observationはqualified L2 4件を確認後、3候補の`provider_unavailable`で`pool_exhausted`となった。これはproduction Worker progressとProvider availabilityのEvidenceであり、既存D1成功証拠やStage 5 live activationを変更しない。Evidenceは[`phase8-live-production-source-contract-r18-20260916.json`](../spec/v2/evidence/phase8-live-production-source-contract-r18-20260916.json)と[`planner-pool-exhaustion-20260916.json`](../spec/v2/evidence/planner-pool-exhaustion-20260916.json)を参照する。
2026-09-17追加: 内部リポジトリ情報を送らないredacted Host-process L2 Planner probeはProvider応答まで到達したが、strict JSONを返さず`model_output_invalid`となった。これは前回の`transport_unclassified`/reconciliationとは別の応答契約失敗であり、同一要求の再送やGate昇格は行わない。`b3630a2`でCLIのbounded分類を明示化し、`19b7770`でfresh requestの`parent_task_id`を失敗projectionへ保持した。Evidenceは[`phase8-planner-redacted-model-output-20260917.json`](../spec/v2/evidence/phase8-planner-redacted-model-output-20260917.json)を参照する。

## 現行Work Addressプログラム

### Gate分離

`D9_DOGFOOD`と`D9_PRODUCTION_DEPLOYMENT`は別トラックとして判定する。
前者はTask Scheduler/OS常駐を要求せず、既存のlocal preconditionが揃った時点で
`execution_readiness=READY_FOR_TRIAL`を示せる。ただしこれは一件限定のHuman-approved
trialを開始できるという意味で、exit Gateの完了ではない。実在するbounded production
repair、Host Verification、pinned local runtime、health、local rollbackまでを実証して
初めて`D9_DOGFOOD=VERIFIED`となる。後者はOS Guardian liveness、crash/reboot recovery、
deployed rolling/rollbackを要求する。現在は前者が実repair・local health・rollbackまで実証済みの`VERIFIED`、後者はHuman指示によりTask Schedulerを保留した`DEFERRED_NOT_READY`である。

Phase 8も`PREPARATION_ONLY`（Role/ownership、local deterministic composition）と
`LIVE_ACTIVATION`（実Providerによる複数Implementerのpatch、review、integration、CI、
`D9_DOGFOOD=VERIFIED`）を分離する。要求は[`02-dogfood-and-production-gates.md`](requirements/process-coordination/02-dogfood-and-production-gates.md)、
decision rationaleは[`ADR-014`](../spec/v2/adr/ADR-014-dogfood-production-gate-separation.md)を参照する。

Stage 4/5の現在の実装境界: Role Manifest/RoleInstance/Commander投影と、temporary-Git/fake-providerによる決定論的Stage 5構成（2 Implementer並列、独立Host Verification、proposal-only Reviewer、`CODE_INTEGRATED`依存解放、Host integration）は検証済みである。live Gemini Workerによるproduction-code統合も2件検証済みだが、これはproduction Worker subgateの証拠であり、Plannerから2 Implementer、Reviewer、依存Taskまでを一つのlive rootで完走した証拠ではない。したがってlive Providerを用いたPhase 8 runtime E2EやPhase 8 activationではなく、既存Commander/Orchestrator境界の局所的な準備証拠として扱う。D9/Stage 1の未達条件を理由なく緩和しない。

現在の開発順序は、既存のTask UUID・dependency・ownership・lease・Gate IDを置換せず、
それらへ表示用のWork Addressを付与する次のStageへ統合する。Stage間は直列、同一Stageの
別laneは依存がない場合だけ並列とする。実行権限は従来どおりCommander/Hostが持ち、
Address自身はAuthorityではない。詳細な契約は[`CODEX_WORK_COORDINATION.md`](CODEX_WORK_COORDINATION.md)を参照する。

- **Stage 1 — Phase 7 closeout / Guardian実運用化**: `1-A`〜`1-C`はProduction Deployment track（OS liveness、deployed rolling、deployed rollback）、`1-D`は共有するproduction approval persistence、`1-E`はDogfood/Productionを分けたreadiness evidenceとする。Task Scheduler/OS登録は現在保留し、local pinned runtimeの証拠と混同しない。
- **Stage 2 — D9 Real Self-Repair**: `2-A` Observation/Diagnosis/Plan、`2-B` candidate、`2-C` Human approval付きreal mutation、`2-D` local rollback drill、`2-E` closure。`execution_readiness=READY_FOR_TRIAL`を開始条件とし、実repair・health・rollback後に`D9_DOGFOOD`をexit Gateとして検証する。`D9_PRODUCTION_DEPLOYMENT`の完了やTask Scheduler登録を前提にしない。production deployment昇格は別Gateのまま保持する。
- **Stage 3 — Autonomy Safety Model**: `3-A` path分類、`3-B` disposable worktree、`3-C` non-Git backup、`3-D` egress、`3-E` Codex policy、`3-F` safety regression。Stage 2完了後に安全モデルを更新する。
- **Stage 4 — Phase 8 Multi-Role Foundation**: `4-A` Role Manifest、`4-B` role instance/ownership、`4-C` resource admission、`4-D` handoff、`4-E` Planner/Implementer/Reviewer role set。新Scheduler/StateStore/Budget/Agent frameworkは追加しない。現在は`PREPARATION_ONLY`。
- **Stage 5 — Multi-Role Runtime E2E**: `5-A` Planner、`5-B` 並列Implementer、`5-C` Reviewer、`5-D` failure/refinement、`5-E` Host integration。Stage 4のcontract成立後、実repositoryで2以上の非重複childを使う。live Provider成功・integration revision・exact-head CIが揃うまで`LIVE_ACTIVATION`へ昇格しない。
- **Stage 6 — AI Company benchmark**: `6-A` scenarios、`6-B` metrics/audit、`6-C` independent evaluator、`6-D` adversarial cases、`6-E` workflow promotion、`6-F` survival modes。Kernel correctnessやAuthorityをbenchmarkへ移さない。
- **Stage 7 — Formal Operation / MCP API**: `7-A` Operation監査、`7-B` planning tools、`7-C` wire transport、`7-D` external-client E2E、`7-E` compatibility。既存Operation/Supervisor authorityへdelegateし、MCP独自Scheduler/Retry/Approvalは作らない。
- **Stage 8 — UI Entry Readiness**: `8-A` read model、`8-B` control model、`8-C` state taxonomy、`8-D` update model、`8-E` redaction、`8-F` headless simulation、`8-G` contract freeze。ここまで完了するまでVirtual Office UIは開始しない。

Stage 1はProduction Deployment trackのOS登録/解除、crash/restart、実rolling/rollbackを
定義するが、Task Scheduler保留中は`DEFERRED_NOT_READY`のまま進めない。Dogfood trackの
実行前提は`READY_FOR_TRIAL`、exit条件は1件の実repair、local health、rollback、exact-head
CIである。Phase 8の`PREPARATION_ONLY`は先行できるが、`LIVE_ACTIVATION`には
`D9_DOGFOOD=VERIFIED`とlive benchmark evidenceを要求する。未検証案をVerified capabilityへ昇格させず、
G6O1、paid provider、OpenAI/Claude API、Production auto-deploy、UI、unbounded loopは従来どおり対象外とする。

## 順序とGate

補足: D2には別内容の bounded documentation Worker sliceも追加で成功した。D6は異なるTask/attemptによる2件のproposal-only Reviewer Shadow比較を取得し、最小比較条件を満たした。D7は別のbounded documentation Taskでlive candidate評価まで通過したが、公式branchの自動integrationは有効化していない。D8の実運用昇格条件は別に判定し、コードの存在や単体テストだけでは昇格しない。

### D0 — Documentation SSOT consolidation

- 目的: Current State、Main Roadmap、Detailed Roadmap、Requirements、ADR、Evidence、作業Planの役割を分離する。
- 入力: 現行repository、`spec/v2/GATE_STATUS.json`、既存Evidence。
- 完了条件: Main RoadmapのNext Targetが一つ、Current Stateが単一の現在状態、完了Planがactive directoryから除去、stale linkなし、分類Inventoryあり。
- 非対象: コード挙動、Gate status、G6O1の昇格。

### D1 — Valid live Free L2 proposal

- 依存: D0。
- 目的: 明示opt-inされたqualified exact L2 resource poolからbounded requestを行い、strict JSON decodeと`RootPlanningProposal`構築を証明する。候補化は、明示的なModel Catalog、Benchmark Catalog、Capability Catalog、既存Runtime State/Qualification/Billing/Privacy/Quota/Healthの別レイヤーを合成する。HTTP 503の明示Unavailable、429、quota、authentication/authorization等のconfirmed failover-safe failureは既存Dispatcherで別のeligible L2へ切替え、transport/timeout/decode/billing等のUNKNOWNはreconciliationへ閉じる。
- Host確認: `parent_task_id`、child count、cycle、dependency type、sensitivity、protected boundary、capability vocabulary。
- pool admission: model名からL2を推測しない。current high-confidence qualification、期限付きbenchmark-derived tier/capability evidence、trusted billing/no-charge authority、quota/privacy/tier admissionをidentity単位で満たす候補だけを使用し、L1への自動降格はしない。Discovery refreshは明示的・read-onlyで、発見だけではrouting admissionにならない。
- 完了Evidence: eligible pool、selection order、provider/binding/model、request/proposal digest、failure category、Host validation結果。既存成功Evidenceは[`planner-l2-live-d1-20260914.json`](../spec/v2/evidence/planner-l2-live-d1-20260914.json)。Configured poolのHost-owned process boundaryを通じた追加成功Evidenceは[`planner-host-dispatch-d1-20260915.json`](../spec/v2/evidence/planner-host-dispatch-d1-20260915.json)。Codex/sandbox内の`local_network_policy_denied`はProvider能力失敗へ算入せず、通常Host processの成功と分離する。Planner authorityは引き続きproposal-only。
- 2026-09-16の追加Host-process observationはProvider結果観測前に`local_network_policy_denied`で拒否され、再試行せず外部transport blockerとして記録した。D1の既存成功Evidence、L2 admission、UNKNOWN/reconciliation境界は変更しない。Evidenceは[`planner-host-network-denial-20260916.json`](../spec/v2/evidence/planner-host-network-denial-20260916.json)。
- その後の明示単一resource observationはProviderまで到達し、`provider_unavailable`で`pool_exhausted`となった。これはネットワーク拒否とは別のProvider availability failureであり、再試行やL1 downgradeは行っていない。Evidenceは[`planner-pinned-provider-unavailable-20260916.json`](../spec/v2/evidence/planner-pinned-provider-unavailable-20260916.json)。
- `gemini:core` / `gemini-3.8-flash`の明示要求は、Provider I/O前の`no_route`で拒否された。現行Host admissionにeligible laneとして露出していないためであり、資格のない代替・L1 downgrade・再試行は行っていない。Evidenceは[`planner-core-no-route-20260916.json`](../spec/v2/evidence/planner-core-no-route-20260916.json)。
- 制約: 自動連打・retry storm・Task作成・Commander書込みなし。

### D2 — Planner → Bridge → Commander → L1 Worker E2E

- 依存: D1。
- 目的: Hostがvalidated proposalをDevelopmentPlanningBridgeでCommander Plan候補へ変換し、既知のqualified Free L1 Workerへ狭いchildを委譲する。
- 完了条件: Plan永続化、Worker実装、Host Verification、ReviewPacket、Codex review、deterministic Host integration、exact revision、focused/full/CI Evidence。既存Evidence: [`planner-to-worker-e2e-20260914.json`](../spec/v2/evidence/planner-to-worker-e2e-20260914.json)。通常Host process境界を使ったproduction `scripts/` Worker taskのWorker-originated integrationも、`gemini:worker:free-3`で完了した。証拠は[`d2-production-worker-integration-20260916.json`](../spec/v2/evidence/d2-production-worker-integration-20260916.json)であり、対象childのCodex direct implementationは0件である。
- 不変条件: `CODE_INTEGRATED`を保持し、未対応dependency typeはfail-closed。対象childのCodex直接実装は成功Evidenceに含めない。2026-09-15には通常Host process境界からproduction `src/` taskをOpenRouter free lane、続いてCloudflare free laneへ独立委譲した。OpenRouterは`reconciliation_required`、CloudflareはHost patch validation、sandbox外Hostでのprovider到達後のinvalid JSONを含むbounded failureとなり、いずれも再送せずD2成功へ算入していない。2026-09-16には資格済みfree-3 laneでproduction `scripts/` task、続いて`src/dev_agent/resources/model_candidates.py`と`src/dev_agent/providers/normalize.py`の2件を完走し、これをproduction Worker subgate successとして算入した。ただし同一rootの2 Implementer並列・Reviewer・依存解放のlive E2Eは未完である。証拠は[`d2-production-worker-reconciliation-20260915.json`](../spec/v2/evidence/d2-production-worker-reconciliation-20260915.json)、[`d2-production-worker-reconciliation-20260915-cloudflare.json`](../spec/v2/evidence/d2-production-worker-reconciliation-20260915-cloudflare.json)、[`d2-production-worker-output-contract-blocker-20260915.json`](../spec/v2/evidence/d2-production-worker-output-contract-blocker-20260915.json)、[`d2-production-worker-integration-20260916.json`](../spec/v2/evidence/d2-production-worker-integration-20260916.json)、[`phase8-live-production-worker-integration-20260916.json`](../spec/v2/evidence/phase8-live-production-worker-integration-20260916.json)。

### D3 — Worker reliability hardening

- 依存: D2の失敗分類。
- 目的: `manifest_input_failure`、`new_file_contract_failure`、`patch_format_failure`、`scope_violation`、`provider_failure`、`model_output_invalid`、`host_verification_failure`を分離し、再現した形式障害だけを最小修正する。
- 完了条件: bounded REWORKとimmutable attemptが機能し、検証を緩めず同種失敗の再発率をEvidenceで比較できる。
- 現在: malformed Python patchの再発を観測し、Worker promptへ構文完結性・区切り文字バランス・既存ファイル用通常diff・JSON escape契約を追加した。さらに同じattempt内の限定的な出力正規化として、Hostがexact base revisionとmanifest範囲を再検査したうえで、bounded textまたはline-arrayの全ファイル内容からdiffを生成できるようにした。Validator緩和、無制限retry、成功Evidenceへの算入は行っていない。2026-09-16のStage 5 live試行ではGeminiのlocal network policy denial、Cloudflare/OpenRouterのpatch・decode・timeout系失敗を記録した一方、後続のfree-3 live production Workerでは2件をHost Verification・Codex review・Host integrationまで完走した。r15のtrailing-whitespaceはHost Verificationで拒否し、strict validatorを維持した。これはD3/production Worker subgateの証拠であり、完全なStage 5 live multi-role activationの証拠ではない。証拠は[`d3-worker-reliability-observation-20260914.json`](../spec/v2/evidence/d3-worker-reliability-observation-20260914.json)、[`d2-production-worker-output-contract-blocker-20260915.json`](../spec/v2/evidence/d2-production-worker-output-contract-blocker-20260915.json)、[`phase8-multirole-live-attempts-20260916.json`](../spec/v2/evidence/phase8-multirole-live-attempts-20260916.json)、[`phase8-live-production-worker-integration-20260916.json`](../spec/v2/evidence/phase8-live-production-worker-integration-20260916.json)。

### D4 — Group D concrete session restart/discovery

- 依存: 既存のsession identity、artifact reference、reconciliation replay。
- 目的: Codex backendで外部sessionを安全に再取得・再開する正式mechanismがある場合だけ接続する。
- 完了条件: start → durable identity → process restart → explicit discovery/reconciliation → same operation recovery、duplicate external startなし。
- 制約: artifactやthread IDからの推測discoveryは禁止。正式mechanismがなければ`UNKNOWN`へ閉じる。
- 現在: `CodexExecBackend`自身のpost-restart discoveryは`NOT_AVAILABLE`。明示的なidentity-bound `BackendDiscoveryAuthority`を注入した場合だけ復旧でき、未提供時は再startせず`UNKNOWN`/reconciliationとする。証拠は[`codex-session-restart-discovery-20260914.json`](../spec/v2/evidence/codex-session-restart-discovery-20260914.json)。該当するsettle poll実装はなく、将来の外部discovery pollを追加する場合は約6秒・有限回・deadline付きとし、request timeoutやUNKNOWN再送とは分離する。

### D5 — MCP runtime thin adapter

- 依存: D2、D4の境界確認。
- 順序: read-only `status` / `artifact_summary` → proposal validation → `run` → mutation tools。
- 完了条件: 既存Commander/Supervisor APIのschema-bound adapter、timeout、redaction、approval、UNKNOWN/reconciliationを確認する。独自Scheduler/Retry/Budget/Authorityは作らない。
- 現在: `src/dev_agent/mcp/runtime.py`のtransport-neutral adapterと、`scripts/devfarm_mcp.py`の固定run/root Supervisor bindingを実装。status/artifact_summary/run/resume/review/rework/integrateを既存`CodexSupervisedCommanderRun`へ委譲し、plan proposal系は未接続のままbounded rejectionとする。wire transportは未接続。証拠は[`mcp-runtime-adapter-20260914.json`](../spec/v2/evidence/mcp-runtime-adapter-20260914.json)。

### D6 — Free L2 Reviewer Shadow

- 依存: 複数のD2成功Evidence。
- 目的: Reviewer proposalとCodex final decisionを比較する。初期はdecision authorityを付与しない。
- 完了条件: agreement、false approve、false reject、missed issue、unnecessary rework、evidence qualityを記録できる。
- 現在: `gemini:worker:free-3` / `gemini-3.6-flash`によるproposal-only live comparisonを、異なるD2 Task/attemptについて2件取得した。いずれもCodex durable decisionとのagreement=true、false approve/reject=false、missed issue=false、unnecessary rework=false、evidence quality=groundedであり、Reviewerにdecision/integration authorityはない。証拠は[`reviewer-shadow-20260914.json`](../spec/v2/evidence/reviewer-shadow-20260914.json)と[`reviewer-shadow-20260914-02.json`](../spec/v2/evidence/reviewer-shadow-20260914-02.json)。

### D7 — LOW/NORMAL Codex-less cycle

- 依存: D6の十分なshadow Evidence。
- 範囲: LOW/NORMAL、非protected、既知task classのみ。official branchへの無人mergeは別Gate。
- 現在: `CodexLessPolicy` とSupervisorのread-only `codexless` CLIを実装し、実Free L1 Workerのbounded documentation変更、独立Host Verification、Free L2 proposal-only review、D6 Shadow gateを一続きで評価した。結果は`CANDIDATE`で、証拠は[`d7-codexless-candidate-20260914.json`](../spec/v2/evidence/d7-codexless-candidate-20260914.json)。Reviewerのdecision authority、公式branchの自動merge/push、Gate昇格、無条件integrationは付与しない。
- D7 candidate evaluator now also admits explicit bounded production task classes (`bounded_bugfix`, `small_helper`, `cli_adapter`, `data_model`) without changing any downstream safety/evidence gate. This only broadens a read-only candidate classification; it does not count as production Worker evidence or enable official-branch Codex-less integration. Evidence: [`d7-bounded-production-task-admission-20260915.json`](../spec/v2/evidence/d7-bounded-production-task-admission-20260915.json).

### D8 — F0–F2 Self-Improvement

- 依存: D7。
- 順序: Observation → Diagnosis → Improvement Planning。既存のCandidate Generation / Validation / Controlled Repairを置換しない。
- 現在: `scripts/devfarm_self_improvement.py` が既存Supervisorのcompact plan/ReviewPacketをHost側で観測し、`observe → diagnose → plan` を`.devfarm/self-improvement/`配下のimmutable bounded artifactへ変換する。F0–F2は既存契約どおりevidence-grounded、`PROPOSAL_ONLY`、Human approval必須であり、model call、dispatch、Task mutation、repair、integrationは接続しない。実compositionの証拠は[`d8-self-improvement-composition-20260914.json`](../spec/v2/evidence/d8-self-improvement-composition-20260914.json)。
- 次: D8のproposal artifactを追加の観測へ適用し、D9へ進む場合もbounded candidate / deterministic validation / approval / rollbackを別Gateとして設計する。D8 compositionを自動修復の証拠へ昇格させない。

### D9 — Controlled Self-Repair

- 依存: D8、既存Host Verification・approval・Gate。
- 完了条件: bounded candidate、deterministic validation、Human/authority boundary、rollback/evidence。
- 現在: `RepairEvidence` と `RepairPolicy`、`RepairExecutionPolicy`、既存Supervisor Host integrationを維持したまま、実在するbounded production issueについてF0–F2、Free L1 Worker、独立Host Verification、Codex review、durable exact approval consume-once、Host integration、pinned local runtime health、known-good rollbackを一件完走した。Evidenceは[`d9-dogfood-real-repair-20260916.json`](../spec/v2/evidence/d9-dogfood-real-repair-20260916.json)。この実証により`D9_DOGFOOD=VERIFIED`へ進めた。
- 2026-09-16の別トラック判定: `D9_PRODUCTION_DEPLOYMENT`は`DEFERRED_NOT_READY`。OS Guardian liveness、deployed crash/reboot recovery、OS配備環境のrolling/rollbackは未検証で、Task Scheduler保留を維持する。従来のnon-mutating readiness review [`d9-real-mutation-readiness-review-20260916-02.json`](../spec/v2/evidence/d9-real-mutation-readiness-review-20260916-02.json)はその不足条件の履歴Evidenceとして保持する。
- 次: D9 Dogfoodのexit後は`AR1`の一回限定Adaptive Refinement live recoveryを行い、続いてD9 Gate依存を満たしたPhase 8 Stage 5 live multi-role実行へ進む。Production Deploymentは別トラックとして保留し、D4のexplicit discovery/UNKNOWN、D5のtransport-neutral authority、既存Recovery境界を維持する。

### D10A/B — Process Coordination foundation

- 目的: D9 real mutationや常駐運用へ進む前に、Task Planeとは別のProcess Coordination Planeへ、作業位置・復帰点・外部送信manifest・generation-fenced requestを記録できるようにする。
- 実装済み: `WorkAddress`、`ResumeCapsule`、bounded LIFO `InterruptStack`、NOTE/PARALLEL/INTERRUPT/CANCEL分類、Host `EgressManifest`、durable `ControlRequest`、`GuardianPolicy`評価、およびgeneration-fenced `GuardianActionService`のbounded action journal。
- 検証: Work Address/Resume/egress/ControlRequest/Guardianのfocused testと全`tests/v2`回帰、Architecture、compileallを通過。Guardian action journalの詳細とexact-head CIは[`guardian-action-journal-20260914.json`](../spec/v2/evidence/guardian-action-journal-20260914.json)、基礎全体の証拠は[`coordination-work-egress-foundation-20260914.json`](../spec/v2/evidence/coordination-work-egress-foundation-20260914.json)。
- G1〜G5実装済み: Guardianの静的profileによるSTART/STOP/RESTART、graceful drain/checkpoint、revision-pinned runtime release、rolling restart、last-known-good rollback composition。deterministic fake/local runtimeのfault testに加えて、G1の実ローカルsubprocess START/STOPをfull regressionで検証し、UNKNOWN結果はreconciliation-requiredとして再送しない。証拠は[`guardian-fault-drill-20260915.json`](../spec/v2/evidence/guardian-fault-drill-20260915.json)と[`guardian-real-local-process-20260915.json`](../spec/v2/evidence/guardian-real-local-process-20260915.json)。
- 未実装/未検証: OS Service/Task Schedulerへの常駐接続、配備後Guardian crash recovery、OS配備環境でのrolling/rollback運用。実processのlocal rolling/known-good rollbackとD9 Dogfoodのlocal pinned runtime promotion/rollbackは個別Evidenceで検証したが、これらをOS配備Evidenceへ昇格しない。
- 追加の安全境界: 標準Worker/Planner/CriticのHost process dispatch、per-dispatch Egress Manifest、transport failure category保持、bounded Host failure diagnostics、明示的terminal Plan supersession、有限6秒Guardian serve、静的Task Scheduler registration dry-runを実装・ローカル検証した。Windows Job Objectによるmanaged child ownershipも実processで確認した。OS登録の実適用と配備後crash recoveryは未検証であり、`WinError 10013`および`reconciliation_required`はモデル能力ではなく外部transport/effect不確実性として扱う。Production Worker successは[`d2-production-worker-integration-20260916.json`](../spec/v2/evidence/d2-production-worker-integration-20260916.json)で別途検証済みである。
- Stage 3 path policy projection: central `classify_path()` now distinguishes `HARD_DENY`, `AUTHORITY_SENSITIVE`, and `NORMAL_REPO`, each EgressManifest records the recomputed class as bounded provenance, and coordination/manifest/Host-test validators reject Windows drive/absolute forms consistently while the legacy protected predicate remains fail-closed. This is a descriptive/input-hardening layer only; egress allow/deny, Worker ownership, integration, and approval boundaries are unchanged. Evidence: [`autonomy-path-classification-20260916.json`](../spec/v2/evidence/autonomy-path-classification-20260916.json)、[`egress-path-classification-20260916.json`](../spec/v2/evidence/egress-path-classification-20260916.json)、[`host-windows-path-boundary-20260916.json`](../spec/v2/evidence/host-windows-path-boundary-20260916.json)。

### D10C/G1 — Guardian static process execution

- 既存Guardian action journalへ、Host設定済み`LaunchProfile`だけを解決する`GuardianProcessExecutor`を接続した。任意commandはControlRequestから生成せず、START/STOP/RESTART以外は境界外とする。
- real local subprocess/fake runtimeで成功、profile/revision不一致、executor失敗→UNKNOWN/no replay、実processのrolling/known-good rollbackを検証した。OS Service/Task Schedulerへの接続と配備後crash recoveryは未検証。
- Work Address `1-A-1`/`1-A-4`として、`guardian os-status` read-only照会、固定Task名のbounded query、raw Task Scheduler output非保存、static registration/disable/unregisterのdry-run、明示`--apply`境界を用意した。既定repository-local registrationはTask Schedulerの261文字`/TR`制限に合わせて長いpath引数を省略し、launcherはSchedulerのworking directoryに依存しない絶対script pathを使う。Human承認後に一度だけ実applyを試行したが、host permissionの`access_denied`で拒否され、taskは作成されなかった。OS登録は未検証のまま維持する。Evidence: [`guardian-os-registration-diagnostic-20260916.json`](../spec/v2/evidence/guardian-os-registration-diagnostic-20260916.json)。

### D9 / Work Address `1-D` — Production approval persistence audit

- Work Address `1-D-1`/`1-D-3`で既存SQLite/JSON StateStoreのapproval repository、expiry、revoke、consume-onceを監査した。`RepairExecutionRequest`はcandidate、task、attempt、base revision、patch/manifest/verification、RollbackProof、review、target、固定`operation_type=repair_integration`を含むauthorization digestへ接続している。新しいapproval DBは作らない。
- 既存のfocused/SQLiteテストで`1-D-3`〜`1-D-6`のcandidate binding、approval再利用拒否、expiry/revoke、consume-once、UNKNOWN後blind retry禁止を確認した。この監査Evidenceは事前のdurable contract確認として保持する。実在candidateに対するHuman approval消費、Host integration、pinned local runtime binding、health、known-good rollbackは[`d9-dogfood-real-repair-20260916.json`](../spec/v2/evidence/d9-dogfood-real-repair-20260916.json)で別途完走し、`D9_DOGFOOD=VERIFIED`となった。OS配備・Task Scheduler・deployed recoveryは引き続き`D9_PRODUCTION_DEPLOYMENT=DEFERRED_NOT_READY`である。Evidence: [`d9-production-approval-audit-20260916.json`](../spec/v2/evidence/d9-production-approval-audit-20260916.json)。

### D10D/G2 — Graceful drain and checkpoint

- `GracefulDrainService`がREADYからDRAININGへ遷移し、claimを止め、既存Resume Capsuleへcheckpointを保存してからRESTARTINGへ進める。外部effectがUNKNOWN/IN_PROGRESSならDRAININGに留め、reconciliation後だけ再開可能とする。
- 6秒pollや不要なsleepは追加していない。外部effect UNKNOWNの再送は禁止。

### D10E/G3 — Revision-pinned runtime

- `RevisionPinnedRuntimeStore`がmutable checkout外のclean Git worktreeへfull commit revisionをmaterializeし、metadata/HEAD/statusを検証する。既存releaseの改変・部分生成・source内release rootは拒否し、実行中revisionの混在を避ける。

### D10F/G4 — Rolling restart composition

- `RollingRestartService`は既存Guardian executorだけを使い、新generationのSTART→health確認→旧generationのSTOPを順序化する。新generation不健康時は旧generationを維持し、停止結果UNKNOWN時は再試行せずreconciliationへ閉じる。

### D10G/G5 — Rollback and fault drill

- `RuntimeRollbackService`がlast-known-good revisionを既存pinned releaseへmaterializeし、G4 rolling compositionへ接続する。release未取得、新generation health失敗、旧generation停止UNKNOWNをbounded resultとして返す。
- G1〜G5のfault cases（start failure、drain中UNKNOWN、Guardian EXECUTING再起動、health failure、old stop failure、duplicate/stale request、mailbox replay、checkpoint recovery、rollback intent duplicate）をlocal/fake testsで検証した。証拠は[`guardian-fault-drill-20260915.json`](../spec/v2/evidence/guardian-fault-drill-20260915.json)。
- これはOS常駐・配備後crash recovery・D9 official runtime mutationの証拠ではない。一件限定Human-approved D9 Dogfood real mutationはverifiedだが、automatic/unrestricted repairとProduction Deploymentは引き続き未解放である。

### D10H — Local Operation runtime coordinator

- `src/dev_agent/operation_runtime.py` の `RuntimeCoordinator` と `scripts/devfarm_runtime_coordinator.py` を、既存 `OperationService` の薄いforeground process boundaryとして実装した。Coordinatorはpeer identity/generation/heartbeatとlocal loopだけを持ち、`prepare_runtime()`、既存`maintenance_tick()`、durable Queue、`WorkerRunner.run_once()`へ委譲する。Task選択、dependency/quota/reconciliation wake、Provider routing、retry、approval、integration authorityは既存Operation/Host境界に残る。
- `health`、`once`、`serve`を提供し、`serve`は既存のidle sleepを使って待機時の処理を軽量化する。stale generationは自身だけを停止し、current generationの共有stop要求を消去しない。restart時もwaiting/reconciliation Taskは既存SQLiteから復元する。
- local foregroundの2周期fake-provider smoke、idle-light behavior、restart persistence、stale-generation fencing、bounded health projectionを検証済み。Evidenceは[`operation-runtime-coordinator-local-20260917.json`](../spec/v2/evidence/operation-runtime-coordinator-local-20260917.json)。OS startup、Task Scheduler/Windows Service登録、配備後Guardian recoveryは別Production Deployment trackとしてDeferredのまま保持する。
- 長時間idle後の決定的local経路については、既存 `maintenance_tick()` が既存のfake/no-quota/available rowだけを60秒間隔で再観測する。実Providerのquota・liveness・UNKNOWN/reconciliationを緩めず、常駐Coordinatorが後から投入された無害なfake Taskを`no_route`へ誤駐車しないことを実processで確認した。Evidenceは[`operation-runtime-local-liveness-refresh-20260917.json`](../spec/v2/evidence/operation-runtime-local-liveness-refresh-20260917.json)。
- 実装commitへpinしたgeneration 3の再起動後も、同じOperation loopがdeterministic fake Taskを1 claim/1 attemptで完了し、旧generationとの重複を作らないことを確認した。これはlocal foreground restartのEvidenceであり、OS startup/Task Scheduler/Windows Service/deployed Guardian recoveryを意味しない。Evidenceは[`operation-runtime-pinned-restart-20260917.json`](../spec/v2/evidence/operation-runtime-pinned-restart-20260917.json)。

### Bounded DevFarm responsibility refactor checkpoint

- `scripts/devfarm_repository.py`、`scripts/devfarm_integration.py`、`scripts/devfarm_review_packet.py` を追加し、共有repository/Git primitive、Host deterministic integration、compact ReviewPacket constructionを明示的なpublic development-only boundaryへ分離した。Commander/Supervisorの既存public seamsには互換wrapperを残したが、authority・approval・Host Verification・UNKNOWN/reconciliation・D9の意味は変更していない。
- `1285 passed, 1 skipped`、Architecture、compileall、およびexact-head CI（kernel 3.10/3.11、provider-smoke）を `59a7f12` で確認した。詳細Evidenceは[`devfarm-service-boundary-refactor-20260915.json`](../spec/v2/evidence/devfarm-service-boundary-refactor-20260915.json)。次のrefactorもサイズではなく依存・所有責務を基準に狭く分割する。
- Assigned Provider reconstruction for Supervisor/MCP resume is now isolated in `scripts/devfarm_resume.py`; MCP uses it directly and Supervisor keeps only a compatibility wrapper. `1286 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-resume-composition-refactor-20260915.json`](../spec/v2/evidence/devfarm-resume-composition-refactor-20260915.json).
- Supervisor and Self-Improvement now share the repository-local bounded JSON reader from `scripts.devfarm_repository`; containment and size/parser errors are no longer duplicated. `1287 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-bounded-json-refactor-20260915.json`](../spec/v2/evidence/devfarm-bounded-json-refactor-20260915.json).
- Latest Worker result projection loading and validation now live in `scripts.devfarm_artifacts`; Worker execution delegates this read-only responsibility. `1288 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-worker-artifact-refactor-20260915.json`](../spec/v2/evidence/devfarm-worker-artifact-refactor-20260915.json).
- Worker manifest loading, task identity binding, and ownership containment now live in `scripts.devfarm_manifests`; Commander and its Host consumers share the boundary. `1289 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-manifest-boundary-refactor-20260915.json`](../spec/v2/evidence/devfarm-manifest-boundary-refactor-20260915.json).

- Commander plan shape, dependency, ownership, assignment, work-address, and delegation validation now live in `scripts.devfarm_plan_validation`; Commander retains compatibility exports only, while persistence, dispatch, verification, recovery, and integration stay separate. `1290 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-plan-validation-refactor-20260915.json`](../spec/v2/evidence/devfarm-plan-validation-refactor-20260915.json).
- Read-only active-plan and legacy ownership projection now lives in `scripts.devfarm_plan_ownership`; Commander retains locking, cross-plan conflict decisions, and ownership authority. `1291 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-plan-ownership-refactor-20260915.json`](../spec/v2/evidence/devfarm-plan-ownership-refactor-20260915.json).
- ReviewPacket and Worker now share `scripts.devfarm_repository.read_json` for bounded repository JSON input; duplicate local artifact readers were removed while Host Verification, Egress, approval, UNKNOWN/reconciliation, and D9 authority remain unchanged. `1292 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-artifact-json-reader-refactor-20260915.json`](../spec/v2/evidence/devfarm-artifact-json-reader-refactor-20260915.json).
- Guardian static profile loading now consumes the same Host-owned repository JSON reader and preserves Guardian-specific type/profile validation. `1293 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-guardian-json-reader-refactor-20260915.json`](../spec/v2/evidence/devfarm-guardian-json-reader-refactor-20260915.json).
- Codex attempt and parallel DevFarm assignment manifest loading now share the Host repository JSON reader; worker validation, attempt identity, and Host failure semantics remain unchanged. `1295 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-manifest-reader-refactor-20260915.json`](../spec/v2/evidence/devfarm-manifest-reader-refactor-20260915.json).
- Immutable verification-record loading now shares the Host repository JSON reader, while invalid-record skipping and plan-validation semantics remain unchanged. `1296 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-verification-reader-refactor-20260915.json`](../spec/v2/evidence/devfarm-verification-reader-refactor-20260915.json).
- `DevFarmError` is now neutral to the CLI module, allowing the repository JSON service and the `devfarm.py` CLI to share one reader without a reverse dependency. Existing public imports remain compatible. `1297 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-error-boundary-refactor-20260915.json`](../spec/v2/evidence/devfarm-error-boundary-refactor-20260915.json).
- Manifest, patch, result, bounded command, digest, and limit validation now live in `scripts.devfarm_contracts`; DevFarm consumers use that neutral boundary while `scripts.devfarm` remains a compatibility/operator entrypoint. The architecture gate rejects future contract imports from the CLI barrel. `1300 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-contract-validation-refactor-20260915.json`](../spec/v2/evidence/devfarm-contract-validation-refactor-20260915.json). This closes the current measured contract-boundary checkpoint; further refactoring must again be selected from dependency/ownership evidence rather than file size.
- Workspace lifecycle operations (`init_farm`, manifest/result persistence, and worktree preparation) now live in `scripts.devfarm_workspace`; internal consumers use that explicit boundary and `scripts.devfarm` remains compatibility/operator entrypoint. Existing task ownership, Host Verification, Egress, integration, and UNKNOWN/reconciliation semantics are unchanged. `1301 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-workspace-boundary-refactor-20260915.json`](../spec/v2/evidence/devfarm-workspace-boundary-refactor-20260915.json). Further refactoring remains responsibility/evidence driven.
- ReviewPacket/ReviewDecision normalization now lives in `scripts.devfarm_review_protocol`; plan validation, ReviewPacket construction, and Supervisor consumers use that neutral contract while Supervisor metadata remains in its existing protocol module for compatibility. Review, D7 candidate, D9, and UNKNOWN/reconciliation authority are unchanged. `1302 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-review-contract-refactor-20260915.json`](../spec/v2/evidence/devfarm-review-contract-refactor-20260915.json).
- Supervisor status, heartbeat, wake-event, and metrics metadata now live in `scripts.devfarm_supervisor_metadata`; `scripts.devfarm_supervisor_protocol` is a compatibility facade, and ReviewPacket/ReviewDecision remain separately owned. No Supervisor action, D7/D9 authority, or UNKNOWN/reconciliation behavior changed. `1307 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-supervisor-metadata-refactor-20260915.json`](../spec/v2/evidence/devfarm-supervisor-metadata-refactor-20260915.json).
- Supervisor CLI parsing and operator command dispatch now live in `scripts.devfarm_supervisor_cli`; `scripts.devfarm_supervisor.main` remains a compatibility entrypoint and direct script invocation remains supported. Existing Supervisor/Commander/Host authority and D7/D9 semantics are unchanged. `1308 passed, 1 skipped` and exact-head CI are recorded in [`devfarm-supervisor-cli-refactor-20260915.json`](../spec/v2/evidence/devfarm-supervisor-cli-refactor-20260915.json).
- Process Coordination live-peer authority now uses the shared `PeerRecord.is_live()` predicate for Guardian sender/target checks and current-peer/snapshot validation. The strict lease boundary (`lease_until > now`) is covered by focused and full regression without changing generation fencing, UNKNOWN/reconciliation, or process authority. Evidence: [`coordination-live-peer-boundary-refactor-20260915.json`](../spec/v2/evidence/coordination-live-peer-boundary-refactor-20260915.json).
- Durable Commander plan persistence, CAS locking, dependency readiness projection, and bounded result recording now use the neutral `scripts.devfarm_plan_state` boundary. Integration, Supervisor, resume, and CLI consumers no longer depend on Commander for plan-state internals; Commander compatibility exports remain. Evidence: [`devfarm-plan-state-refactor-20260915.json`](../spec/v2/evidence/devfarm-plan-state-refactor-20260915.json).
- Standard Worker, Supervisor, and Commander host-process compositions now use the shared `route_through_host()` adapter. The adapter does not add routing/retry authority; in-process behavior and Host-owned Egress/UNKNOWN boundaries are unchanged. Evidence: [`devfarm-host-composition-refactor-20260915.json`](../spec/v2/evidence/devfarm-host-composition-refactor-20260915.json).
- Worker, Codex backend, and deterministic Host integration now share the bounded `scripts.devfarm_repository.git_process`/`git_bytes` boundary. Existing compatibility seams remain, and Git invocation refactoring does not change task ownership, approval, Egress, Host Verification, or UNKNOWN/reconciliation semantics. The full regression at this checkpoint is `1320 passed, 1 skipped`; Evidence: [`devfarm-git-refinement-refactor-20260915.json`](../spec/v2/evidence/devfarm-git-refinement-refactor-20260915.json).
- Adaptive Refinement now accepts a Host-ranked bounded `alternate_binding_ids` sequence while preserving the legacy single-binding field. The policy consumes only one same-tier candidate per action and remains selection/retry/integration-authority free; this enables future qualified model diversity without a second retry framework. Evidence: [`devfarm-git-refinement-refactor-20260915.json`](../spec/v2/evidence/devfarm-git-refinement-refactor-20260915.json).

### Main Phase 8 / Main Phase 9

- D9後にmanifest-defined multi-role / AI Company benchmarkへ進む。
- Virtual Office UIは正式Operation/MCP APIが安定した後に限る。

## 凍結・非ブロッキング

G6O1-SIM / G6O1-LIVE、real paid-provider qualification、OpenAI/Claude API、OS-level sandbox、Production auto-deploy、unbounded autonomous loop、Discord/Virtual Office UIは、Humanが明示的に再開するまで次Task候補から除外する。G6O1は`DEFERRED_FROZEN`、`NOT VERIFIED`、`roadmap_blocking=false`を維持し、元の受入条件は`spec/v2/G6O1_DEFERRED.md`と`spec/v2/GATE_STATUS.json`に残す。Compression ServiceはHuman指示で凍結解除済みだが、固定`semantic-dense-v1`のpayload-only最適化に限り、G6O1のbilling/evidenceへ接続しない。live smoke未検証の証拠は[`compression-service-connection-20260914.json`](../spec/v2/evidence/compression-service-connection-20260914.json)に残す。

## 完了判定の原則

コード、local test、Host Verification、Git integration、remote push、exact-head CIは別Evidenceである。モデルの成功報告、adapterの存在、schema-only MCP、Provider request到達だけでGateを昇格しない。
