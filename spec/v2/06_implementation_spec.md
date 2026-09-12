# 実装仕様（現行 Phase 7）

この文書は現行 v2 の実装契約と依存方向を定義する。配置を素早く探すための索引は `docs/SYSTEM_MAP.md`、運用手順は `docs/CODEX_COMMANDER.md`、現在の実装証跡は `docs/CURRENT_STATE.md` と分担する。alpha0/Phase 0 の候補配置を現行仕様として扱わない。

## 実装境界

| 領域 | 実装場所 | 主責務 |
| --- | --- | --- |
| Domain | `src/dev_agent/domain/` | Task、Model、Tool、Event の typed protocol |
| Security / Policy | `src/dev_agent/security/`、`src/dev_agent/intelligence/` | scope、audit、Evaluator、finite escalation、authority policy、execution target seam |
| State | `src/dev_agent/state/` | SQLite connection/transaction owner、core/effect repository |
| Persistence primitive | `src/dev_agent/persistence/lease.py` | State／Schedulerが共有するLeaseProof、StaleLease、atomic fence assertion |
| Tools | `src/dev_agent/tools/` | Tool policy、executor、effect guard、timeout/cancel |
| Resources | `src/dev_agent/resources/` | ResourceLedger facade、catalog/observation/quota/health/budget、qualification projection、trusted billing catalog、explicit repair audit、router/control、schema/migrations、unknown-quota admission store |
| Providers | `src/dev_agent/providers/` | Adapter、Factory、Registry、Dispatcher、journal。`ollama_cloud`／`vercel`は`openai_compatible`のHTTP boundaryを再利用し、local `ollama`とは別identity。Gemini追加keyは`api_key_env`／`project_id`／`quota_domain`をnon-secret binding metadataとして持つ |
| AgentBackend | `src/dev_agent/backends/` | 外部Agent harnessとのthin typed contract、`BackendAdmission`付きdispatcher。実adapterは別slice |
| Handoff | `src/dev_agent/handoff/`、`scripts/handoff_cycle.py` | model-neutralなControl/Payload envelope、role Protocol、kinotch-ja-v1 renderer、development-onlyの1-cycle composition。独立Compression ServiceやTask stateは所有しない |
| Compression | `src/dev_agent/compression/` | 固定profileの独立HTTP client、payload-only compression、digest/provenance、機械的情報保持検査。任意LLM proxyやProvider routingは所有しない |
| Runtime | `src/dev_agent/runtime/` | Controller、model turn、legacy compatibility、checkpoint/resume |
| Scheduler | `src/dev_agent/scheduler/` | DurableQueue、WorkerRunner、lease、quota wake/requalification |
| Operation | `src/dev_agent/operation.py`、`operation_bootstrap.py`、`operation_planning.py`、`cli.py`、`src/dev_agent/__main__.py` | 人間向け start/submit/status/stop、maintenance、既存 Evaluator/Lifecycle の明示 composition |
| Recovery | `recovery/` | Runtime から独立した backup/restore/diagnostics/repair boundary |
| DevFarm | `scripts/devfarm*.py`、`.devfarm/` | development-only proposal、verification、Commander parent plan |
| Formal contract | `spec/v2/` | API/implementation contract、requirements、ADR、Gate、traceability |

`.devfarm/` は runtime の正式データ領域ではなく、development-only の ignored artifact である。Credentials、`.env*`、private key、budget authority、Recovery、Gate status は保護領域として扱う。

Provider billing projectionは`TrustedResourceProfile.billing_mode`を使用する。`free_fixed`、`recurring_allowance`、`recurring_credit`、`paid`、`unknown`を混同せず、allowance-backed bindingを`cost_minor=0`というprovider名だけの近道で登録しない。`no_charge_guaranteed`はcurrent trusted profileと`hard_stop` overage policyからのみ導出し、応答costが欠落してもこの証拠なしに0円確定しない。Ollama Cloud／Vercelのexact model profileとlive qualificationが揃うまでは構成可能だが、production qualification済みとは扱わない。

## 許可された依存方向

下位の実装は上位の責務を直接飛び越えず、次の一方向を基本とする。

```text
domain
  ↑
policy / security
  ↑
state / tools / resources / providers / intelligence
  ↑        ↑
persistence (lease primitive only)
  ↑
runtime
  ↑
scheduler / operation composition
```

- `domain` は runtime、provider SDK、SQLite 実装を import しない。
- `policy/security` は domain を利用できるが、runtime の具体実装を所有しない。
- `state`、`tools`、`resources`、`providers`、`intelligence` は domain と policy の typed 契約を利用できるが、互いの内部実体を直接参照しない。
- `persistence/lease.py` は State と Scheduler が共有する小さなSQLite安全primitiveだけを所有し、StateからScheduler concrete implementationをimportしない。
- `runtime` は上記 facade/public protocol を composition する。Provider 通信は `ProviderDispatcher`、Resource 操作は `ResourceControlPlane` を経由する。
- `scheduler` は Queue/Worker/lease と runtime lifecycle を接続するが、Provider SDK の分岐や新しい Task state machine を所有しない。lease claim統計とlogical execution retryは別カウンタとして保持する。
- `operation` は既存部品を composition する薄い入口であり、production scheduler を並立させない。
- Operationの内部変更理由は、`operation_bootstrap.py`（composition）、`operation_planning.py`（proposal／dependency）、`cli.py`（CLI parsing）、`state/control_repository.py`（durable stop control）へ分離する。`operation.py`は外部互換facadeとして残す。
- `operation` は起動時に既存 Resource を再構成・上書きせず、trusted billing catalog と operator-owned quota domain を検証する。Provider の正常応答／bounded quota probe が Resource observation freshness の唯一の更新入口であり、未知価格・未観測quotaは fail-closed とする。timeout後のlate provider successは同一effect intentへreconcileしてから、保存済み応答を通常Controller経路へreplayする。
- `intelligence`／`operation` はTaskのcanonical execution capability、competency、policy traitを分類し、Routerへはexecution capabilityだけを渡す。qualification projectionは期限内のexact provider／binding／modelかつhigh confidenceから導出し、model名heuristicや未知文字列でproduction routeを許可しない。provider execution saturationはbinding lane単位または全eligible lane時のpool wait reasonへ写像する。
- `backends` は外部Agent harnessのidentity、session、event、cancellation、resultをtyped化し、既存StateStoreのeffect intent／Event／reconciliationへ接続する薄い境界である。`AgentBackendDispatcher`は既存authorityの証拠を`BackendAdmission`として要求し、lease／budget／approval／privacyの各strict-`True` flagとTask／Backend identityのcapability coverageをstart前に検証する。Runtime/State/Scheduler/Budget/Recoveryの所有権を持たず、Backend固有adapterはこの境界の外側に置く。
- `intelligence/target.py` の `ExecutionTargetPolicy` は ModelProvider と AgentBackend の実行先を分離する。通常はModelProviderを選び、AgentBackendは明示autonomy、approval、budget、privacy、capabilityの既存証拠が揃った場合だけ許可する。tierだけを理由に自動昇格しない。
- `recovery/` は runtime/controller から独立し、durable artifact と operator authority を扱う。Recovery が Controller の内部状態を書き換える設計にしない。
- `devfarm` は production scheduler/state/authority と独立した development-only 層で、既存 WorkerRunner/ProviderFactory 等の公開境界を composition できるが、公式 branch を自動変更しない。
- `handoff` は role、instruction、constraints、payload、referenceの型とrendererだけを提供する。Planner/Executor/Reviewer orchestration、Budget、Scheduler、Task lifecycleを所有しない。Controlはpayload compressionへ渡さず、G6O1-SIM/LIVEのbilling identityも別のResource authorityで管理する。
- `compression` は固定`semantic-dense-v1` profileのHTTP transport、response integrity、payload-only provenance、機械的 retention warningを提供する。Compression Service本体、任意system prompt、Provider routing、Budget authorityを所有しない。
- `handoff_cycle.py` はdevelopment-onlyの薄いcompositionで、既存DevFarm/Codex attemptを一回呼び、Reviewer handoffをHumanへ返して停止する。新しいScheduler、retry state machine、durable Task state、automatic integrationを追加しない。
- `scripts/check_architecture.py` と `scripts/test_scope.py` はread-onlyの開発preflightであり、runtime authority、StateStore、Schedulerを所有しない。affected-test mapはfull regressionの代替ではない。

## 禁止される実装

- v1 の `Executor`、`LLMClient`、`RecursionManager` を v2 runtime から import しない。
- Provider Adapter から runtime、Controller、Gate、budget authority を import しない。
- Resource Store、State Repository、Tool Executor から Controller を参照しない。
- Router から Dispatcher を参照せず、Dispatcher から Router の内部 ledger を直接操作しない。
- Recovery と DevFarm に production scheduler、独立 durable state machine、独自の authority を追加しない。
- OSS Agent framework、MCP、A2A、UI を導入して既存の state/queue/authority を二重化しない。
- 任意 Python import、無制限 shell、infinite retry、blind quota retry、未知の外部効果の自動 replay を安全機構として扱わない。

## 状態・証跡契約

- schema migration は既存の ordered migration owner を通し、不要な schema version を追加しない。
- critical transition は StateStore の transaction owner と `commit_transition` を通す。
- 外部 dispatch は intent、budget reservation、provider audit、lease/fencing を結び、timeout/decode/ownership不明は UNKNOWN/reconciliation として保存する。late completionがknown successになった場合は同じintentをatomicにreconcileし、Queue wake後に新規外部dispatchなしでdurable responseを再処理する。unknownのままならwakeせず、再送もしない。
- FiniteLifecycle の使用数は durable Task/Event history から復元し、process restart で retry 上限をリセットしない。
- Quota は observation の `quota_domain` と reset/blocked_until を正本とし、reset 到達だけで復帰させず、bounded probe と正常観測の永続化後に routing へ戻す。
- cancellation は Task payload の競合する全置換だけに依存せず、append-only control record を terminal transition 直前に再読込する。Provider health は selected resource/binding、quota wake は `quota:<domain>` に限定する。
- waiting reasonはwake authorityとrestart behaviorを持つ。provider saturationはmatching binding capacity、全lane飽和時は`resource:provider_execution_saturated:pool`を任意laneのcapacity recoveryでwakeする。quotaはmatching domainのbounded requalification、late provider completionは同一effect intentのdurable replayだけがwakeし、unknown outcomeを別dispatchへ変換しない。
- Evidence-based routing は現段階では advisory とし、minimum sample、freshness、rollback 条件を満たすまで hard routing policy に接続しない。
- Operation の lifecycle composition は `OperationService` が `EvaluationCoordinator`、`FiniteLifecycleLoop`、`TaskLifecycleCoordinator`、`EscalationExecutor` を composition する。Operation はこれらの内部state machineを複製せず、reviewed dispatchには `DurableQueue` の lease proofを要求する。
- Root planning は `src/dev_agent/intelligence/planner.py` のproposal／validatorを使い、Task作成前に既存`TaskGraph`制約と親子privacyを検証する。依存childは独立schedulerを作らず、`WAITING_DEPENDENCY`としてStateStoreに保存する。
- Planner dependencyは依存keyごとに`ARTIFACT_READY`、`TASK_COMPLETED`、`CODE_INTEGRATED`を保存する。`CODE_INTEGRATED`は`integration_status=INTEGRATED`と非空`integration_revision`を要求し、旧文字列dependencyは`TASK_COMPLETED`として後方互換に扱う。
- Resource schema v10の`resource_repairs`はoperator明示migrationのbefore/after auditを保持する。通常Operation startupはcatalogをrepair/upsertせず、Qualificationのlow/medium confidenceは観測としてのみ扱いroutingへ投影しない。

## 実装・検証ルール

変更は focused tests、`tests/v2` full regression、関連する read-only Gate check の順で検証する。Provider の live qualification は CI へ混ぜず、operator 実行と compact な evidence artifact で管理する。Gate を evidence artifact だけで昇格させず、Current State と Traceability の所有文書を同じ変更群で同期する。
