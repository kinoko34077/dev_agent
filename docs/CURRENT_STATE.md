# Current State — v2/bootstrap

実装基準は `69320dd` です。本書はそのコードと、直近の外部資格化・DevFarm
実行結果を同期したCurrent Stateです。GATE_STATUSの既存statusは変更していません。

## 判定

- Phase 6 foundation: `VERIFIED`
- Phase 6 operational: `G6O2`〜`G6O6` は `VERIFIED`
- `G6O1`: `BLOCKED_EXTERNAL`（実paid Providerのworst-case課金実証と、deployment-owned budget設定の外部保護が必要）
- Phase 7A/B: typed task profile、bounded tier policy、明示opt-in resource routing、model identityとthinking effortの分離を実装済み
- Phase 7C/D: deterministic host evaluator、durable evidence、有限escalation plan、明示review、dispatch-ready handoffを実装済み
- Phase 7 execution: `EscalationExecutor`がaccepted `dispatch_ready`を再検証し、既存ProviderDispatcher・effect intent・budget/resource境界を通る有限dispatchを実装済み。`EvaluationDispatchCoordinator`がhost evaluator→明示review→dispatchの一回のcycleを接続し、PASS／拒否／unknownを別状態で返す。重複再送とunknown/reconciliationをfail-closedに扱う
- Phase 7E: bounded workflow promotion proposalの生成境界を実装済み。自動promotionは行わない
- Phase 7 lifecycle: host evaluator／reviewed dispatchの結果を、`TaskLifecycleCoordinator`が冪等な`commit_transition()`でterminal／retry／approval／reconciliation状態へ適用する境界を実装済み
- Phase 7 Operation Layer: `python -m src.dev_agent` の`start`／`submit`／`status`／`stop`を追加し、既存のSQLiteStateStore・DurableQueue・WorkerRunner・Controller・ProviderDispatcherをcompositionした。StateStoreとQueueは同じSQLiteファイルを共有し、CLI停止は実行中Taskを即時失敗扱いせず、durableな協調キャンセル要求または既存のreconciliation状態を維持する
- Phase 6 quota operation: ResourceLedger schema v8でmetric／window／reset source／blocked-until／block reasonを保持し、ProviderErrorの429／quota／transport分類をrouting blockへ接続済み。blocked observationは新しい正常観測で明示的に復帰する。Scheduler queue schema v4と`QuotaWakeScheduler`はreset boundaryへのdurable parking／wakeを提供し、`QuotaRequalificationCoordinator`は呼出側が明示した一回のbounded probeについて、freshな正常観測の保存後だけdue taskをwakeする。Provider再probeの自動loopやclockだけによるblock解除は行わない
- DevFarm orchestration: Remote proposalとHost verificationを分離し、remote inference枠とworktree verification枠を別Governorでboundedに制御する。proposal失敗時にworktreeを作成せず、自動mergeもしない
- Gate昇格やlive qualificationの成功は、local test・model自己申告・Worker proposalだけから推測しない

## 検証

- v2ローカル全回帰: `434 passed, 1 skipped`（`python -m pytest tests/v2 -q --durations=10`、所要時間は実行環境依存）
- Operation Layer focused: `8 passed`（submit／status、canonical Dispatcher経由のstart、queue復旧、provider非依存safe stop、durable cancellation request）
- Evaluator→dispatch cycle focused: `18 passed in 2.62s`
- intelligence routing / escalation execution focused: `26 passed in 1.28s`
- DevFarm manifest / patch / host verification focused: `24 passed in 27.50s`
- DevFarm host verification: Gemini 3.5 Flash-Lite `gemini-worker-phase7-003` が、入力ファイルを外部送信せず、隔離worktreeへpatchを適用し、許可済みhost test `7 passed` を確認
- DevFarm 2 Worker並列: `gemini-worker-parallel-a` と `gemini-worker-parallel-b` が別worktree・別所有ファイルで同時実行され、各 `7 passed`、`result_accepted=true` を確認。実測はそれぞれ1.528秒、1.278秒
- Worker metricsはhost側で `provider_id`、`provider_binding_id`、`model_id`、`intelligence_tier`、request id、elapsed、許可されたusage scalar、host test結果を記録する。Modelのtests claimは証拠に採用しない
- quota/reset focused regression: `55 passed`（quota policy、schema v8 migration、blocked routing、DevFarm remote/host concurrency）
- skip: `tests/v2/test_budget_reservations.py:142`（Windows ACLはdeployment-owned）
- 最新コード基準のexact-head GitHub Actionsは、push後に`v2-core`（Python 3.10/3.11）と`v2 tests`を外部観測する。repo内GATE_STATUSへCI結果を書き戻してexact-headを自己参照しない

## Provider状態

| Provider / binding | 状態 | tier / role | 備考 |
| --- | --- | --- | --- |
| Gemini `gemini:core` / `gemini-3.8-flash` | `QUALIFIED` | L2 / core | text、ToolCall、ToolResult、multi-turn、thoughtSignature roundtrip、Controller E2E、audit、budget reconciliation。証跡: [`gemini-3.8`](../spec/v2/evidence/gemini-3.8-flash-qualification.json) |
| Gemini `gemini:worker` / `gemini-3.5-flash-lite` | `QUALIFIED` | L1 / Worker | 同上のcanonical qualification。証跡: [`gemini-3.5-Lite`](../spec/v2/evidence/gemini-3.5-flash-lite-qualification.json) |
| Gemini `gemini:compat` / `gemini-2.5-flash` | `QUALIFIED` | compatibility / verified fallback | 既存live evidenceを維持 |
| Cloudflare Workers AI / `cloudflare` | `QUALIFIED` | L1 / free cloud | canonical経路、ToolCall、audit、budget reconciliation。quotaは未報告値をunknownのまま保持 |
| OpenRouter Free / `openrouter:free` | `QUALIFIED` | L1 / late fallback | `openrouter/free`のcanonical経路、ToolCall、audit、budget reconciliation。quotaは未報告 |
| Ollama / `ollama` | `QUALIFIED` | privacy / survival | local実Provider |
| Groq | `UNQUALIFIED` | — | `/v1/models` probeがHTTP 403。permission/account状態を推測しない |
| Mistral | `UNQUALIFIED` | — | 推論HTTP 429。成功や無料枠を推測しない |
| SambaNova | `INACTIVE` | — | `/v1/models`は到達したが推論HTTP 429/402。free/no-charge qualification対象外 |
| Gemini `gemini:fast-fallback` / `gemini-3.7-flash` | `UNQUALIFIED` | L1/L2 candidate | 構成候補としてのみ文書化し、DevFarm/Routerへactivateしていない |

資格情報は環境変数または外部secret storeからのみ読み込み、repo・manifest・audit・
証跡へ値を書き込みません。Gemini固有のFunctionCall part、FunctionResponse、
thoughtSignature、thinking設定はAdapter内部で保持・変換し、Kernel protocolへ漏らしません。

## Phase 7 実行境界

- `EscalationExecutor`はControllerへ実装を追加せず、accepted review、exact plan/dispatch identity、Task state、lease、Intelligence policy、tier、capability、privacy、quota、budget、bindingを再確認してからcanonical `ProviderDispatcher`へ委譲します
- `RETRY_SAME`は同一binding、`RETRY_OTHER_PROVIDER`は同tierの別binding、`ESCALATE`はdurable allowed tier内のnext tierを選びます。`plan_id`、`dispatch_id`、`task_id`、attempt、bindingをeffect intentとdurable eventへ結合し、succeededは再送せず、dispatching/unknown/reconcilingは再実行せずreconciliationへ残します
- `IntelligenceRoutePolicy`はtierとthinking effortを別フィールドで出力します。L1はminimal、通常L2はlow、難しいL2/L3はhigh。Gemini AdapterだけがGemini 3.xの`thinkingConfig.thinkingLevel`へ変換します
- `EvaluationDispatchCoordinator`はhost evaluator結果を一回の明示review済みdispatchへ接続します。host test、最終的なTask terminal transition、次cycleのevidence生成は呼出側が所有し、自動無限retry、model自己昇格、自動mergeはありません

## Refactor Freezeの内容

- Controllerのprovider request実行を `runtime/model_turn.py`、compatibility direct-provider実行を `runtime/legacy_provider.py` へ分離。canonical経路は `Controller -> ProviderDispatcher` のままです
- ResourceLedgerは同一SQLite connection / lock / transaction semanticsを維持し、Catalog、Observation、Quota、Health、Budget Reservation storeを内部分離しました。schema v8でquotaのmetric／window／reset／blocked stateをordered migrationしています
- SQLiteStateStoreはconnection / transaction ownerを維持し、`state/schema.py`、`state/core_repository.py`、`state/effects_repository.py`へ内部整理しました
- ToolRuntimeは `tools/executor.py` と `tools/effect_guard.py`へ実行／副作用責務を分離し、timeout、process-tree kill、cancellation、approval、idempotency、reconciliation semanticsを維持しました
- ProviderRegistryは `providers/registry.py` を責務所有者とし、DispatcherはControlPlaneのSnapshot API経由でrouting/budget viewを取得します
- DevFarmはworktree不存在・base revision不一致・dirty状態・symlink/out-of-root・protected path・secret outbound・scope外patch・binary/submodule/symlink patch・patch上限超過をfail-closedで拒否します
- `src` と `tests/v2` の旧v1トップレベルimportは0件。v1実行資産は `legacy/v1-final` に隔離済みです

## DevFarm状態

開発WorkerはCodex/operatorが明示起動した場合だけ動作し、manifestの`external_provider_allowed`、
`approved_provider_ids`、`outbound_files`を境界にします。read可能範囲と外部送信範囲は別で、
workspace外へresolveするpath、protected/credential/secret path、secret候補を含むsourceは拒否します。
patchは実変更pathをunified diffから決定し、worktreeへだけ適用します。patch末尾LFのような
非意味的transport正規化はmetricsへ記録し、silent truncateは行いません。

Proposalはrepository rootからmanifestのoutbound scopeだけを読み出すRemote stageで、Host
verification時に初めて専用worktreeを作成します。`DevFarmOrchestrator`はremote inferenceと
worktree verificationを別々のbounded governorで管理し、remoteを最大4、Hostのworktree／pytest等を
小さい枠に保ちます。remote／hostの枠を0にした場合もfail-closedです。

`gemini-worker-phase7-003` は入力ゼロの新規doc patchをhost-verifiedしました。さらに
`gemini-worker-parallel-a` / `gemini-worker-parallel-b` は独立file ownershipの2 Worker並列を
host-verifiedしました。いずれも生成物は`.devfarm/results/`（ignore対象）に保持し、smoke用の
dummy docを公式branchへ自動統合していません。実装成果の公式統合はCodexがreviewし、必要性を
確認した変更だけを行います。

## 次の作業

1. Operation Layerのprocess restart／resumeとterminal／waiting／reconciliation E2Eを追加し、外部providerを使う明示operator実行でもstatus／auditを確認できるようにする
2. reset-aware quotaをProvider別の実観測・blocked_until・`QuotaRequalificationCoordinator`のbounded probe／wakeへ接続し、429をblind retryしないScheduler境界をProviderごとの運用入口へ仕上げる
3. Worker metricsを一定数蓄積し、`Task Type × tier × capability × quota × latency/failure`の実績ベースroutingを、最小サンプル数・期限・rollback条件付きで導入する
4. `TaskLifecycleCoordinator`の結果を次cycleのhost evidenceと有限のPASS / retry / escalation / WAIT_HUMAN循環へ接続し、再開時のacceptanceを追加する
5. AgentBackend / Codex、MCP、Self-Improvementは前段のPhase 7 acceptanceが揃うまで着手しない

G6O1は実paid Providerとdeployment-owned budget configurationという外部条件待ちであり、
コード不足として勝手に昇格しません。`README.md`は入口、`PHASE6_PLAN.md`はPhase 6受入条件、
`V2_EXECUTION_PLAN.md`はロードマップ、`CHANGELOG.md`は履歴、`TRACEABILITY.md`は要求と実装所有者の
追跡に限定します。
