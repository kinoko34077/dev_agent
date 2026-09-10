# Current State — v2/bootstrap

実装基準は `dfc34f6` です。テスト契約同期を含む最新検証HEADは `0582abc` で、
本書はそのコードと、直近の外部資格化・DevFarm実行結果を同期したCurrent Stateです。
GATE_STATUSの既存statusは変更していません。

## 判定

- Phase 6 foundation: `VERIFIED`
- Phase 6 operational: `G6O2`〜`G6O6` は `VERIFIED`
- `G6O1`: `BLOCKED_EXTERNAL`（実paid Providerのworst-case課金実証と、deployment-owned budget設定の外部保護が必要）
- Phase 7A/B: typed task profile、bounded tier policy、明示opt-in resource routing、model identityとthinking effortの分離を実装済み
- Phase 7C/D: deterministic host evaluator、durable evidence、有限escalation plan、明示review、dispatch-ready handoffを実装済み
- Phase 7 execution: `EscalationExecutor`がaccepted `dispatch_ready`を再検証し、既存ProviderDispatcher・effect intent・budget/resource境界を通る有限dispatchを実装済み。`EvaluationDispatchCoordinator`がhost evaluator→明示review→dispatchの一回のcycleを接続し、PASS／拒否／unknownを別状態で返す。重複再送とunknown/reconciliationをfail-closedに扱う
- Phase 7E: bounded workflow promotion proposalの生成境界を実装済み。自動promotionは行わない
- Phase 7 lifecycle: host evaluator／reviewed dispatchの結果を、`TaskLifecycleCoordinator`が冪等な`commit_transition()`でterminal／retry／approval／reconciliation状態へ適用する境界を実装済み
- Phase 7 finite lifecycle: `FiniteLifecycleLoop`が既存のevaluator／review／dispatch／lifecycle境界を明示的な有限cycleへ合成する。評価回数上限を持ち、review・evidence・dispatchは呼出側が供給し、自動承認・自動再送・モデル自己昇格は行わない
- Phase 7 integration: CommanderのWorker proposalはmanifestに固定されたGit commit objectを読み、作業中のcheckout HEAD進行に影響されない。通常のcode dependencyは依存Taskの`INTEGRATED`までreleaseせず、FiniteLifecycleのcycle使用数はdurable evaluation historyから再構築する
- Phase 7 evidence routing: `EvidenceBasedRoutingPolicy`がhost-verified Worker metricsを、最小sample数・証拠期限・受入率／retry rollback条件付きで、呼出側から渡されたhard-filter済みbindingの範囲だけで順位付けする。証拠不足・期限切れ・回帰は採用せず、ResourceRouterのcapability／privacy／quota／budget hard filterや通常routingを上書きしない。自動routingへの接続は未実施
- Phase 7 Operation Layer: `python -m src.dev_agent` の`start`／`submit`／`status`／`stop`を追加し、既存のSQLiteStateStore・DurableQueue・WorkerRunner・Controller・ProviderDispatcherをcompositionした。StateStoreとQueueは同じSQLiteファイルを共有し、CLI停止は実行中Taskを即時失敗扱いせず、durableな協調キャンセル要求または既存のreconciliation状態を維持する
- Operation hardening: Operation起動時の既存Resourceはread-onlyで保持し、binding×model×trusted catalogにない価格を無料と推測しない。Cloud Resourceはoperator-ownedな`quota_domain`を明示し、初回はlive probeなしでhealthy扱いせず、正常Provider応答／正常quota probeだけがResource freshnessを更新する。`DispatchDenied`はbudget／quota／maintenance／resource wait／invalid failureへ意味別に遷移する
- Cross-process safety hardening: cancellation requestはappend-onlyの`task_controls`へ保存し、terminal transition直前に再読込してlate completionをfenceする。Provider healthはresource/binding単位、quota wakeは`quota:<domain>`単位で、別Resource／別domainのTaskを誤って起こさない
- Phase 6 quota operation: ResourceLedger schema v8でmetric／window／reset source／blocked-until／block reasonを保持し、ProviderErrorの429／quota／transport分類をrouting blockへ接続済み。blocked observationは新しい正常観測で明示的に復帰する。Scheduler queue schema v4と`QuotaWakeScheduler`はreset boundaryへのdurable parking／wakeを提供し、`QuotaRequalificationCoordinator`は呼出側が明示した一回のbounded probeについて、freshな正常観測の保存後だけdue taskをwakeする。Operation Layerの`maintenance_tick`がdue domainだけを対象にprobe上限を適用し、`start`／`start --once`からも同じmaintenance boundaryを通る。OpenAI互換adapterはtelemetryを返す場合だけ既存`/models` probeからquota observationを返し、typed probe failureには保守的cooldownを永続化する。Provider再probeの無制限loopやclockだけによるblock解除は行わない
- DevFarm orchestration: Remote proposalとHost verificationを分離し、remote inference枠とworktree verification枠を別Governorでboundedに制御する。proposal失敗時にworktreeを作成せず、自動mergeもしない
- Development Commander: `scripts/devfarm_commander.py`が既存DevFarmの上にdevelopment-only親Planを提供する。`.devfarm/plans/<run-id>.json`へobjective、base revision、Task、依存、非重複ownership、assignment、result参照をdurably保存し、plan／dispatch／status／collect／verify／resume／reassign／mark-integratedを既存境界のcompositionで提供する。Taskごとの固定revisionを許容し、code dependencyは明示的な`mark-integrated`後だけreleaseする。Production Runtimeのstate／Scheduler／authorityやAgentBackendではない
- Commander dogfood: `phase7-commander-local-dogfood-004`で、`aa2f819`固定のproposal、隔離worktreeでのHost Verification（許可済みfocused test `1 passed`）、Codex review、明示integrationを一連のPlanとして完了した。これはCommanderの計画・依存・検証・統合境界の実証であり、外部Cloud Workerの資格化や成功を意味しない
- Gate昇格やlive qualificationの成功は、local test・model自己申告・Worker proposalだけから推測しない

## 検証

- v2ローカル全回帰: `511 passed, 1 skipped`（`python -m pytest -q tests/v2 --durations=10`、95.89秒。所要時間は実行環境依存）
- Operation hardening focused: `94 passed, 1 skipped`（Operation、quota、DevFarm attempt、SQLite contention、security、budget境界）
- Operation Layer focused: `12 passed`（submit／status、canonical Dispatcher経由のstart、queue復旧、process restart、terminal／waiting reconciliation、provider非依存safe stop、durable cancellation request、due quota maintenance／wake、startからのmaintenance境界）
- Evaluator→dispatch cycle focused: `18 passed in 2.62s`
- finite lifecycle focused: `11 passed`（明示review、dispatch、terminal transition、評価cycle上限、process restart後のdurable cycle／waiting boundary）
- intelligence routing / escalation execution focused: `26 passed in 1.28s`
- DevFarm manifest / patch / host verification focused: `24 passed in 27.50s`
- Commander focused: `4 passed`（親Plan、ownership／dependency validation、dispatch／collect／Host Verification、bounded reassign、CLI status）
- Commander dogfood: `phase7-commander-local-dogfood-004`のWorker成果をHost Verified後にCodexが明示統合。host testは`1 passed`、metricsは`provider_id=local-harness`のdurable artifactへ記録
- Operation external E2E: Cloudflare `@cf/meta/llama-3.1-8b-instruct`で`submit`、`start --once`、ToolCall／ToolResult、final response、durable `task.completed`、provider audit成功2件を確認。証跡: [`phase7-operation-cloudflare`](../spec/v2/evidence/phase7-operation-cloudflare-2026-09-10.json)
- Phase 7 integration acceptance: Commander parallel baseline、`INTEGRATED` dependency、FiniteLifecycle restart、quota reset→bounded probe→wake、Operation external E2E、Commander dogfoodを確認済み。Evidence routingは実Provider Worker metricsのminimum sample／freshness／rollback証拠が揃うまで`DEFERRED_ADVISORY`とし、ResourceRouterへhard接続しない。実AgentBackend adapter／MCPはこの条件の完了後に着手する
- AgentBackend boundary: `src/dev_agent/backends/protocol.py`に外部Agent harnessのidentity、scoped request、session、event stream、cancellation、completion／failure／unknown／reconciliation resultだけを定義した。Codex App Server等の実adapter、dispatch、MCPは未着手で、既存Runtime／State／Scheduler／Budget／Recoveryの所有権を移していない
- AgentBackend dispatch boundary: `src/dev_agent/backends/dispatcher.py`が既存SQLiteStateStoreのeffect intent、durable Event、explicit reconciliationを使い、task／scope／authorityを検証してから外部Backendを起動する。COMPLETED済みの重複dispatchは再実行せず、UNKNOWN／RECONCILINGは明示reconcileまで再送しない。実Codex adapter、Host Verification、MCPは未着手である
- AgentBackend focused: protocol `7 passed`、dispatcher `12 passed`、合計 `19 passed`（identity復元、scope／authority拒否、session重複防止、event sequence、cancel、UNKNOWN、restart、reconciliation）
- Evidence routing focused: `5 passed`（minimum samples、hard-filter済みbinding限定、期限切れ、rollback threshold、malformed evidence拒否、latest timestamp）
- DevFarm host verification: Gemini 3.5 Flash-Lite `gemini-worker-phase7-003` が、入力ファイルを外部送信せず、隔離worktreeへpatchを適用し、許可済みhost test `7 passed` を確認
- DevFarm 2 Worker並列: `gemini-worker-parallel-a` と `gemini-worker-parallel-b` が別worktree・別所有ファイルで同時実行され、各 `7 passed`、`result_accepted=true` を確認。実測はそれぞれ1.528秒、1.278秒
- Worker metricsはhost側で `provider_id`、`provider_binding_id`、`model_id`、`intelligence_tier`、`task_type`、request id、elapsed、許可されたusage scalar、host test結果を記録し、`.devfarm/metrics.sqlite3`へ`task_id + request_id`単位で冪等に蓄積する。Modelのtests claimは証拠に採用しない。metricsはrouting候補の観測値であり、Policyやacceptanceを上書きしない
- Commander/Worker履歴 hardening: Commander Planは`plan_revision`付きCASで並行更新を検出し、Worker結果は`attempt_id`ごとのimmutable artifactと履歴を正本とする。検証時はrootの最新投影へフォールバックせず、選択attemptのpatchを必須として読む
- Protected policy / audit hardening: protected responsibility path、PathPolicyの最長prefix、secret semantic sanitizerを共有境界へ集約し、token使用量・session telemetryは保持しつつcredential値だけをredactする
- quota/reset focused regression: `56 passed`（quota policy、schema v8 migration、blocked routing、bounded typed probe failure、DevFarm remote/host concurrency）
- SQLite contention: 独立processのqueue／state／stop／status同時操作、WAL、5秒bounded busy timeoutを確認。既存のWindows ACL skipは継続
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

Commander dogfoodでは、外部Providerへsourceを送らない決定的local harnessを使って
`phase7-commander-local-worker-004`を実行しました。`aa2f819`からのproposalを専用worktreeで
検証し、`tests/v2/test_commander_dogfood_local_004.py`の`1 passed`をHost側で確認した後、
Codexがreview・公式branchへ統合しました。Cloudflare／OpenRouter／Geminiの別試行は
proposal品質または応答失敗でHost Verifiedに至っておらず、外部Free Worker成功とは扱っていません。

## 次の作業

1. Commander dogfoodとCloudflareのOperation external E2Eは完了。次はProvider別のquota probe callbackで取得できるtelemetryだけを使い、reset復帰を外部またはfixtureで確認する。未提供値はunknownのまま扱う
2. Worker metricsのhost側SQLite蓄積と、hard-filter済みbindingだけを対象とする期限／minimum sample／rollback付きadvisory順位付けは実装済み。`DEFERRED_ADVISORY`条件が満たされるまでResourceRouterへhard接続しない
3. Phase 7 acceptanceは上記統合境界を確認済み。Evidence routingのsample条件を満たした時点で再監査し、実Codex AgentBackend adapter／MCPは各専用Gateで開始する
4. G6O1は実paid Providerとdeployment-owned budget configurationという外部条件待ちであり、Phase 7コード判定と混ぜない

G6O1は実paid Providerとdeployment-owned budget configurationという外部条件待ちであり、
コード不足として勝手に昇格しません。`README.md`は入口、`PHASE6_PLAN.md`はPhase 6受入条件、
`V2_EXECUTION_PLAN.md`はロードマップ、`CHANGELOG.md`は履歴、`TRACEABILITY.md`は要求と実装所有者の
追跡に限定します。
