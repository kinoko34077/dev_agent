# dev_agent v2 詳細ロードマップ

この文書は、[V2_EXECUTION_PLAN.md](V2_EXECUTION_PLAN.md) が示す現在の大きな順序を、実装可能なGateと依存関係へ展開する詳細正本である。要求・ADR・Evidenceを複製せず、各Gateの完了条件と参照先だけを持つ。

## 現在位置

`D0 Documentation SSOT consolidation`、`D1 Valid live Free L2 proposal`、`D2 Planner → Bridge → Commander → L1 Worker E2E` は、2026-09-14のbounded live evidenceで完了した。D1は実Free L2のstrict JSON proposalとHost validation、D2は同proposalからBridge、Commander Plan、Free L1 Worker、独立Host Verification、Codex review、Host deterministic integrationまでを証明している。D4はCodexExecBackendに正式な外部discoveryがないため、明示DiscoveryAuthority以外の復元を行わずUNKNOWNへ閉じる境界を確認した。D5のtransport-neutral thin runtime adapterまで進め、wire transportは未接続である。D6は異なる2件のproposal-only Reviewer Shadow比較を取得し、D7の決定的candidate policy/CLIと、D8 F0–F2のproposal-onlyデータ契約を実装した。さらにD7の実Free Worker → Host Verification → Free L2 proposal-only → Host candidate評価を証拠化した。D10のG1〜G5として、静的Guardian process実行、graceful drain/checkpoint、revision-pinned release、rolling restart、last-known-good rollback compositionとfault drillをHost/localで検証した。直近では標準Worker/Planner/Critic dispatchをHost-owned one-shot boundaryへ寄せ、transport/egress証跡、明示Plan supersede、六秒既定のGuardian serve、OS登録dry-runを追加した。DevFarmは、plan query、assigned Provider composition、Worker prompt、Worker output/metrics projectionの限定責務分離まで進めた。ただし公式branchへのCodex-less統合、OS常駐Guardian、配備後crash recovery、D9 real mutationは未接続である。

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
- 制約: 自動連打・retry storm・Task作成・Commander書込みなし。

### D2 — Planner → Bridge → Commander → L1 Worker E2E

- 依存: D1。
- 目的: Hostがvalidated proposalをDevelopmentPlanningBridgeでCommander Plan候補へ変換し、既知のqualified Free L1 Workerへ狭いchildを委譲する。
- 完了条件: Plan永続化、Worker実装、Host Verification、ReviewPacket、Codex review、deterministic Host integration、exact revision、focused/full/CI Evidence。既存Evidence: [`planner-to-worker-e2e-20260914.json`](../spec/v2/evidence/planner-to-worker-e2e-20260914.json)。次の未完了受入条件は、通常Host process境界を使ったproduction `src/`/`scripts/` Worker taskのWorker-originated integrationであり、対象childのCodex direct implementationは0件とする。
- 不変条件: `CODE_INTEGRATED`を保持し、未対応dependency typeはfail-closed。対象childのCodex直接実装は成功Evidenceに含めない。2026-09-15には通常Host process境界からproduction `src/` taskをOpenRouter free lane、続いてCloudflare free laneへ独立委譲したが、いずれもpatch生成前に`reconciliation_required`へ閉じたため、再送せずD2成功へ算入していない。証拠は[`d2-production-worker-reconciliation-20260915.json`](../spec/v2/evidence/d2-production-worker-reconciliation-20260915.json)と[`d2-production-worker-reconciliation-20260915-cloudflare.json`](../spec/v2/evidence/d2-production-worker-reconciliation-20260915-cloudflare.json)。

### D3 — Worker reliability hardening

- 依存: D2の失敗分類。
- 目的: `manifest_input_failure`、`new_file_contract_failure`、`patch_format_failure`、`scope_violation`、`provider_failure`、`model_output_invalid`、`host_verification_failure`を分離し、再現した形式障害だけを最小修正する。
- 完了条件: bounded REWORKとimmutable attemptが機能し、検証を緩めず同種失敗の再発率をEvidenceで比較できる。
- 現在: malformed Python patchの再発を観測し、Worker promptの構文完結性・区切り文字バランス契約だけを追加した。Validator緩和、無制限retry、成功Evidenceへの算入は行っていない。証拠は[`d3-worker-reliability-observation-20260914.json`](../spec/v2/evidence/d3-worker-reliability-observation-20260914.json)。

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

### D8 — F0–F2 Self-Improvement

- 依存: D7。
- 順序: Observation → Diagnosis → Improvement Planning。既存のCandidate Generation / Validation / Controlled Repairを置換しない。
- 現在: `scripts/devfarm_self_improvement.py` が既存Supervisorのcompact plan/ReviewPacketをHost側で観測し、`observe → diagnose → plan` を`.devfarm/self-improvement/`配下のimmutable bounded artifactへ変換する。F0–F2は既存契約どおりevidence-grounded、`PROPOSAL_ONLY`、Human approval必須であり、model call、dispatch、Task mutation、repair、integrationは接続しない。実compositionの証拠は[`d8-self-improvement-composition-20260914.json`](../spec/v2/evidence/d8-self-improvement-composition-20260914.json)。
- 次: D8のproposal artifactを追加の観測へ適用し、D9へ進む場合もbounded candidate / deterministic validation / approval / rollbackを別Gateとして設計する。D8 compositionを自動修復の証拠へ昇格させない。

### D9 — Controlled Self-Repair

- 依存: D8、既存Host Verification・approval・Gate。
- 完了条件: bounded candidate、deterministic validation、Human/authority boundary、rollback/evidence。
- 現在: `RepairEvidence` と `RepairPolicy` により、F2 `PROPOSAL_ONLY` plan、独立Host Verification、既知の外部結果、attempt単位のtrust/approval、安全な変更範囲、rollback referenceを決定的に検査し、proposal-only `CANDIDATE`を返す。`RepairExecutionRequest` / `RepairExecutionPolicy` と `scripts/devfarm_self_repair.py` の薄いadapterは候補・attempt・ReviewDecision・対象・approval引数を再照合し、既存ApprovalPolicyとSupervisor Host helperへ接続する。公開された `build_repair_candidate()` を通じ、実D7 Free WorkerのHost-verified artifactを一件、proposal-only candidateへmaterializeした。Fake Workerを使う一時Git repositoryでは、approval再照合・既存ApprovalPolicyの一回消費・Host integration commitまでの決定的compositionも検証済み。D10 G5では、revision-pinned last-known-good releaseをrolling replacementへ接続するHost/local rollback compositionとfault drillを追加した。Evidenceは[`d9-repair-candidate-policy-20260914.json`](../spec/v2/evidence/d9-repair-candidate-policy-20260914.json)、[`d9-repair-approval-preflight-20260914.json`](../spec/v2/evidence/d9-repair-approval-preflight-20260914.json)、[`d9-repair-host-integration-20260914.json`](../spec/v2/evidence/d9-repair-host-integration-20260914.json)、[`d9-repair-live-worker-candidate-20260914.json`](../spec/v2/evidence/d9-repair-live-worker-candidate-20260914.json)。production approval store、real repair execution、OS/production rollback、official branch integrationは未実施。
- 次: 実修復へ進む場合も、先にD10A/BおよびG1〜G5（D10C〜D10G）のProcess Coordination/Guardian前提を満たす。並行するDevFarm refactorは実際の依存・責務境界に限定し、既存のpublic authorityを保ったまま続ける。既存DevFarmの新immutable attempt、Host Verification、明示Human approval、Git-backed integration/rollbackを一つずつ接続する。adapterの存在だけで実行・Gate昇格を行わず、approval消費後の結果は既存Recovery境界で扱う。

### D10A/B — Process Coordination foundation

- 目的: D9 real mutationや常駐運用へ進む前に、Task Planeとは別のProcess Coordination Planeへ、作業位置・復帰点・外部送信manifest・generation-fenced requestを記録できるようにする。
- 実装済み: `WorkAddress`、`ResumeCapsule`、bounded LIFO `InterruptStack`、NOTE/PARALLEL/INTERRUPT/CANCEL分類、Host `EgressManifest`、durable `ControlRequest`、`GuardianPolicy`評価、およびgeneration-fenced `GuardianActionService`のbounded action journal。
- 検証: Work Address/Resume/egress/ControlRequest/Guardianのfocused testと全`tests/v2`回帰、Architecture、compileallを通過。Guardian action journalの詳細とexact-head CIは[`guardian-action-journal-20260914.json`](../spec/v2/evidence/guardian-action-journal-20260914.json)、基礎全体の証拠は[`coordination-work-egress-foundation-20260914.json`](../spec/v2/evidence/coordination-work-egress-foundation-20260914.json)。
- G1〜G5実装済み: Guardianの静的profileによるSTART/STOP/RESTART、graceful drain/checkpoint、revision-pinned runtime release、rolling restart、last-known-good rollback composition。deterministic fake/local runtimeのfault testに加えて、G1の実ローカルsubprocess START/STOPをfull regressionで検証し、UNKNOWN結果はreconciliation-requiredとして再送しない。証拠は[`guardian-fault-drill-20260915.json`](../spec/v2/evidence/guardian-fault-drill-20260915.json)と[`guardian-real-local-process-20260915.json`](../spec/v2/evidence/guardian-real-local-process-20260915.json)。
- 未実装/未検証: OS Service/Task Schedulerへの常駐接続、配備後Guardian crash recovery、実processのrolling/rollback運用、D9 official runtime mutation。これらを実証するまでD9 real mutationを解放しない。
- 追加の安全境界: 標準Worker/Planner/CriticのHost process dispatch、per-dispatch Egress Manifest、transport failure category保持、bounded Host failure diagnostics、明示的terminal Plan supersession、有限6秒Guardian serve、静的Task Scheduler registration dry-runを実装・ローカル検証した。Windows Job Objectによるmanaged child ownershipも実processで確認した。OS登録の実適用、配備後crash recovery、実production Worker成功は未検証であり、`WinError 10013`および`reconciliation_required`はモデル能力ではなく外部transport/effect不確実性として扱う。

### D10C/G1 — Guardian static process execution

- 既存Guardian action journalへ、Host設定済み`LaunchProfile`だけを解決する`GuardianProcessExecutor`を接続した。任意commandはControlRequestから生成せず、START/STOP/RESTART以外は境界外とする。
- real local subprocess/fake runtimeで成功、profile/revision不一致、executor失敗→UNKNOWN/no replayを検証した。OS Service/Task Schedulerへの接続は未検証。

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
- これはOS常駐・配備後crash recovery・D9 official runtime mutationの証拠ではない。D9 real mutationは引き続き未解放。

### Main Phase 8 / Main Phase 9

- D9後にmanifest-defined multi-role / AI Company benchmarkへ進む。
- Virtual Office UIは正式Operation/MCP APIが安定した後に限る。

## 凍結・非ブロッキング

G6O1-SIM / G6O1-LIVE、real paid-provider qualification、OpenAI/Claude API、OS-level sandbox、Production auto-deploy、unbounded autonomous loop、Discord/Virtual Office UIは、Humanが明示的に再開するまで次Task候補から除外する。G6O1は`DEFERRED_FROZEN`、`NOT VERIFIED`、`roadmap_blocking=false`を維持し、元の受入条件は`spec/v2/G6O1_DEFERRED.md`と`spec/v2/GATE_STATUS.json`に残す。Compression ServiceはHuman指示で凍結解除済みだが、固定`semantic-dense-v1`のpayload-only最適化に限り、G6O1のbilling/evidenceへ接続しない。live smoke未検証の証拠は[`compression-service-connection-20260914.json`](../spec/v2/evidence/compression-service-connection-20260914.json)に残す。

## 完了判定の原則

コード、local test、Host Verification、Git integration、remote push、exact-head CIは別Evidenceである。モデルの成功報告、adapterの存在、schema-only MCP、Provider request到達だけでGateを昇格しない。
