# Codex Supervisor運用

この文書は、既存のdevelopment-only CommanderをCodexが少ない判断回数で
進めるための運用境界を定義する。Production RuntimeのScheduler、Task state、
Budget、Authority、AgentBackendを置き換えない。

## 境界

`CodexSupervisedCommanderRun`（`scripts/devfarm_supervisor.py`）は、既存の
`CommanderPlanStore`、`dispatch_plan`、`collect_plan`、`verify_plan`、
`reassign_task`、`mark_integrated`をcompositionする薄いfacadeである。
`advance()`はsnapshot取得用の一回のbounded pass、`run_until_intervention()`は
同じPlanを内部で再開し続けるblocking入口である。新しいScheduler、常駐daemon、
retry state machineは作らない。

`advance()`はboundedな一回のpassだけを実行する。同期的な既存DevFarm proposalは
その呼出しの中で完了まで待つため、正常系ではWorker処理中にCodex推論を増やさない。
再起動後などresultのない`DISPATCHED`が残った場合は、durableなdispatch deadlineを
越えるまで`WAITING_FOR_WORKER`とし、期限後は`orphaned_dispatch`として再実行せず
reconciliation要求へ送る。LLMやCodexのraw会話は保存・再送しない。

`run_until_intervention()`はこのpassをWorker完了・Host Verification・review要求・
terminal failure・overall deadlineのいずれかまで内部継続する。待機中はCodexを
pollせず、Planのcadence（1 / 5 / 10 / 15分）だけでsleepする。これはSupervisorの
Worker待機hintであり、外部API/session discoveryのsettle pollではない。後者を追加する
場合は約6秒・有限回・deadline付きとし、request/provider timeoutやUNKNOWN外部効果の
再送とは分離する。呼出元へ戻るのはreview、Human判断、完了、または安全に継続できない
境界である。

## Supervisor metadata

既存`.devfarm/plans/<run_id>.json`のoptionalな`supervisor` projectionへ、次を保存する。

- `status`、`roadmap_reference`、`roadmap_position`
- `cadence_minutes`（1 / 5 / 10 / 15）
- `unchanged_check_limit`（最大3）と`unchanged_check_count`
- `next_action`、boundedな`wake_events[]`
- `review_packets[]`、`review_decisions[]`（attempt、evidence、decisionをdurableに記録）
- Codex wake/review、Worker dispatch/success/retry、payload/artifact参照のcompact metrics

`status`／`run`の出力には、PlanのTask責務から導出した`delegation` summaryも含まれる。
`worker_owned_task_count`、`codex_owned_task_count`、`worker_integrated_task_count`を
集計し、`worker_candidate=true`を明示したCodex Taskだけを
`codex_direct_implementation_count`へ数える。そのTaskには具体的な
`delegation_reason`が必須で、`codex_direct_reasons`へ出る。レビュー・統合・
architecture担当をCodexの実装実績へ推測変換しない。summaryは新しいauthorityやDB列
ではなく、既存PlanからHost側で再計算するcompact projectionである。

同じwakeはkind/task/attempt/digestでdedupeする。raw conversation、patch、stdout、
stderr、巨大PayloadをSupervisor metadataへ保存しない。planの既存revision CASを
通して保存するため、会話再開後もPlanから再構築できる。

Heartbeatの時間幅は待機をLLMのbusy loopにしないための補助情報である。残り時間の
目安は1 / 5 / 10 / 15分へboundedに写像し、同一結果が続くと最大15分まで段階的に
延長する。これはOS timerやWorker完了イベントを偽装するものではなく、外部の呼出し
機構が同じPlanを再開する際の推奨値である。

作業アドレス、Resume Capsule、ユーザー割込みの分類、Host-owned egress manifestは
[`CODEX_WORK_COORDINATION.md`](CODEX_WORK_COORDINATION.md)の番号付き契約に従う。
Supervisorはそれらを既存Plan/Coordinationへ投影するだけで、Task Scheduler、process
authority、外部送信の安全判定を新設しない。

## 処理順

```text
load / refresh
  ↓
READY Workerを既存DevFarmへdispatch
  ↓
result artifactをcollect
  ↓
PROPOSEDだけを既存Host Verificationへ渡す
  ↓
HOST_VERIFIEDならCodex review wakeを記録
  ↓
Codexのreview decisionをdurably記録
  ├─ APPROVE_INTEGRATION → deterministic Host integration helper
  ├─ REWORK → 差分Handoff付きの新attempt manifest
  ├─ REJECT → terminal rejection
  └─ ESCALATE → Human decision
```

`HOST_VERIFIED`だけでは自動integrationしない。UNKNOWN、approval、budget、privacy、
protected path、STATIC_ONLYの制約を緩和しない。実行可能なHost Verificationは既存の
attempt単位operator approvalを要求し、外部Workerの自己申告を証拠にしない。
`integrate_approved_worker()`を使う場合、Codexは採否だけを判断し、Hostが検証済み
patchを再読込して適用・commitし、既存`mark_integrated()`のGit証拠へ接続する。

## CLI

```text
python scripts/devfarm_supervisor.py status <run-id> --root .
python scripts/devfarm_supervisor.py resume <run-id> --root .
python scripts/devfarm_supervisor.py run <run-id> --root . \
  --trust-level TRUSTED_HOST_EXEC --operator-approved
python scripts/devfarm_supervisor.py review <run-id> <task-id> --root . \
  --attempt-id <attempt-id> --decision APPROVE_INTEGRATION \
  --evidence-ref <verification-artifact>
python scripts/devfarm_supervisor.py rework <run-id> <task-id> --root . \
  --failure-evidence-ref <failure-artifact> \
  --required-correction "<durable correction>"
python scripts/devfarm_supervisor.py integrate <run-id> <task-id> --root . \
  --decision-id <decision-id> --target-checkout . \
  --target-ref HEAD --commit-message "<message>"
```

`status`はcompactなPlan/Supervisor metadataだけを出力する。`resume`は既存のProvider
activation境界を通して一回だけ`advance()`する。`run`は同じactivation境界で
`run_until_intervention()`を呼ぶblocking運用入口である。既定trust levelは
`STATIC_ONLY`で、明示されたattempt承認なしに外部生成コードをHost実行しない。
`dispatch_timeout_seconds`、`max_wait_seconds`、Planの`overall_deadline`で待機はbounded。
`review`、`rework`、`integrate`は新しい判断・統合エンジンではない。各々、既存の
`record_review_decision()`、`rework_handoff()` + `reassign()`、
`integrate_approved_worker()`へ渡す薄いCLI adapterである。`review`の決定は
`APPROVE_INTEGRATION`、`REWORK`、`REJECT`、`ESCALATE`に限定される。`rework`は
現在attemptに結び付いたdurableな`REWORK`決定が無ければ失敗し、`integrate`は
durableな承認とHost側Git証拠を再検証する。

日常の外部Worker dogfoodで`TRUSTED_HOST_EXEC`を使う場合も、承認はその実行対象の
attemptに限る。既定値を変更したり、承認を全Planへ永続化したりしない。通常の短い
運用手順と再開規則は[`docs/CODEX_DAILY_DOGFOOD.md`](CODEX_DAILY_DOGFOOD.md)にまとめる。

## D7 / D8 bounded boundaries

複数のcleanなReviewer Shadow比較が揃った後も、Codex-less判定は低risk・非protected・
既知Task classだけを対象とする。`python scripts/devfarm_supervisor.py codexless ...`は
既存PlanのReviewPacket、Host Verification、manifest、Shadow evidenceを読み、
`CANDIDATE`または`REJECTED`を返すread-only境界である。ReviewerやCLIにintegration、
push、merge、Gate昇格の権限は与えず、公式branchへの自動変更は行わない。

Self-ImprovementのF0〜F2は、`ObservationRecord` → `ImprovementDiagnosis` →
`ImprovementPlanProposal`のbounded proposal契約から始める。raw output/secretは受け付けず、
DiagnosisはObservationのevidenceへgroundedで、Planは常にHuman approvalを要求する。
これらは既存DevFarm、Task、Scheduler、Repair、Host authorityへ自動接続しない。

## Handoff / payload

再作業は初回指示全文を複製せず、既存Task reference、failure evidence、review finding、
required correctionだけを`rework_request()`で渡し、`reassign()`が旧manifestを履歴へ残した
新manifestへ接続する。外部本文を使う場合も
`ExternalTextReference`のHTTPS・SHA-256・size・expiry metadataだけをHandoffへ保持し、
取得・upload・権限発行はこの層の責務にしない。外部本文はPayloadであり、Controlを上書きしない。

Compressionは`HttpCompressionService`の固定endpoint・固定`semantic-dense-v1` profileを
通常Handoff compositionから利用できる。import時I/Oは行わず、`COMPRESSION_API_TOKEN`がある
場合だけfactoryを遅延compositionする。3,000 Unicode code points以下は送信せず、超過時も
Controlを除いたPayloadだけを送る。構造上限1,000,000とprovider-safe limit 200,000を分離し、
provider-safe limit超過は送信前にbounded configuration failureへ閉じる。tokenなし・設定不備は
最適化用途では原文へfallbackできる。CompressionはG6O1、Provider pool、Budget authorityへ
接続しない。live smokeの認証未検証状態は
[`spec/v2/evidence/compression-service-connection-20260914.json`](../spec/v2/evidence/compression-service-connection-20260914.json)に記録する。

MCP runtimeは[`src/dev_agent/mcp/runtime.py`](../src/dev_agent/mcp/runtime.py)のtransport-
neutral adapterと、既存Supervisorへ束ねる[`scripts/devfarm_mcp.py`](../scripts/devfarm_mcp.py)
で構成する。wire transport、Planner proposal/applyのauthorityはこのsliceへ追加しない。

## Process Coordination foundation

Agent/Codexのprocess-level presenceとhandoffは、Task Planとは別の
`src/dev_agent/coordination/`へ置く。`PeerRecord`はrole、instance、generation、
revision、heartbeat、leaseを持ち、旧generationを拒否する。`CoordinationStore`は
別SQLiteでMailboxのat-least-once claim/ACK/idempotencyを保持し、
`CoordinationArtifactStore`はSHA-256・size・revision付きのimmutable artifactを保存する。
`ProcessCoordinationService`はこの三者の薄いcompositionだけを提供する。

このfoundationはAgent/Codexを起動・停止・killせず、Guardian、OS service、drain、
rolling restart、revision-pinned runtime、rollback、D9 real repairも有効化しない。
正式なCodex external session discoveryがない場合は、既存D4方針どおりUNKNOWN/
reconciliationに閉じる。Coordination DBをTask stateへ統合したり、raw会話をmemory SSOTへ
保存したりしない。
