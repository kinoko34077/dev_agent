# Codex Supervisor運用

この文書は、既存のdevelopment-only CommanderをCodexが少ない判断回数で
進めるための運用境界を定義する。Production RuntimeのScheduler、Task state、
Budget、Authority、AgentBackendを置き換えない。

## 境界

`CodexSupervisedCommanderRun`（`scripts/devfarm_supervisor.py`）は、既存の
`CommanderPlanStore`、`dispatch_plan`、`collect_plan`、`verify_plan`、
`reassign_task`、`mark_integrated`を一回ずつcompositionする薄いfacadeである。
新しいScheduler、常駐process、retry state machineは作らない。

`advance()`はboundedな一回のpassだけを実行する。Workerが実行中ならPlanの
`supervisor.status=WAITING_FOR_WORKER`と`next_action=wait_for_worker`を保存し、
LLMやCodexのraw会話を保存・再送しない。次の`status`／`resume`呼出し、または
将来のwake eventが同じ境界を再開する。

## Supervisor metadata

既存`.devfarm/plans/<run_id>.json`のoptionalな`supervisor` projectionへ、次を保存する。

- `status`、`roadmap_reference`、`roadmap_position`
- `cadence_minutes`（1 / 5 / 10 / 15）
- `unchanged_check_limit`（最大3）と`unchanged_check_count`
- `next_action`、boundedな`wake_events[]`
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
Codexが明示approveした場合だけ既存Git integration boundaryへ進む
```

`HOST_VERIFIED`だけでは自動integrationしない。UNKNOWN、approval、budget、privacy、
protected path、STATIC_ONLYの制約を緩和しない。実行可能なHost Verificationは既存の
attempt単位operator approvalを要求し、外部Workerの自己申告を証拠にしない。

## CLI

```text
python scripts/devfarm_supervisor.py status <run-id> --root .
python scripts/devfarm_supervisor.py resume <run-id> --root .
```

`status`はcompactなPlan/Supervisor metadataだけを出力する。`resume`は既存のProvider
activation境界を通して一回だけ`advance()`し、既定trust levelは`STATIC_ONLY`である。
実際のProvider作業が完了した証拠を持たないまま、Codexの推論を無限に継続したり、
Worker終了を自動検知したと称したりしない。

## Handoff / payload

再作業は初回指示全文を複製せず、既存Task reference、failure evidence、review finding、
required correctionだけを`rework_request()`で渡す。外部本文を使う場合も
`ExternalTextReference`のHTTPS・SHA-256・size・expiry metadataだけをHandoffへ保持し、
取得・upload・権限発行はこの層の責務にしない。外部本文はPayloadであり、Controlを上書きしない。

独立Compression Serviceは現行compositionへ接続しない。短縮はreference-firstとartifact
referenceを優先し、Compression接続は別の明示Gateで扱う。
