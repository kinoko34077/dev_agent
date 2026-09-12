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
pollせず、Planのcadence（1 / 5 / 10 / 15分）だけでsleepする。呼出元へ戻るのは
review、Human判断、完了、または安全に継続できない境界である。

## Supervisor metadata

既存`.devfarm/plans/<run_id>.json`のoptionalな`supervisor` projectionへ、次を保存する。

- `status`、`roadmap_reference`、`roadmap_position`
- `cadence_minutes`（1 / 5 / 10 / 15）
- `unchanged_check_limit`（最大3）と`unchanged_check_count`
- `next_action`、boundedな`wake_events[]`
- `review_packets[]`、`review_decisions[]`（attempt、evidence、decisionをdurableに記録）
- Codex wake/review、Worker dispatch/success/retry、payload/artifact参照のcompact metrics

同じwakeはkind/task/attempt/digestでdedupeする。raw conversation、patch、stdout、
stderr、巨大PayloadをSupervisor metadataへ保存しない。planの既存revision CASを
通して保存するため、会話再開後もPlanから再構築できる。

Heartbeatの時間幅は待機をLLMのbusy loopにしないための補助情報である。残り時間の
目安は1 / 5 / 10 / 15分へboundedに写像し、同一結果が続くと最大15分まで段階的に
延長する。これはOS timerやWorker完了イベントを偽装するものではなく、外部の呼出し
機構が同じPlanを再開する際の推奨値である。

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
```

`status`はcompactなPlan/Supervisor metadataだけを出力する。`resume`は既存のProvider
activation境界を通して一回だけ`advance()`する。`run`は同じactivation境界で
`run_until_intervention()`を呼ぶblocking運用入口である。既定trust levelは
`STATIC_ONLY`で、明示されたattempt承認なしに外部生成コードをHost実行しない。
`dispatch_timeout_seconds`、`max_wait_seconds`、Planの`overall_deadline`で待機はbounded。

## Handoff / payload

再作業は初回指示全文を複製せず、既存Task reference、failure evidence、review finding、
required correctionだけを`rework_request()`で渡し、`reassign()`が旧manifestを履歴へ残した
新manifestへ接続する。外部本文を使う場合も
`ExternalTextReference`のHTTPS・SHA-256・size・expiry metadataだけをHandoffへ保持し、
取得・upload・権限発行はこの層の責務にしない。外部本文はPayloadであり、Controlを上書きしない。

独立Compression Serviceは現行compositionへ接続しない。短縮はreference-firstとartifact
referenceを優先し、Compression接続は別の明示Gateで扱う。
