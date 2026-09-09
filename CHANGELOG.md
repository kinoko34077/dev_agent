# Changelog

このファイルは、`dev_agent` の v1 保全と v2 再構築について、チャット上で確認した方針・実施結果と、リポジトリに確定した変更を時系列で記録する。

## [Unreleased] — v2/bootstrap

### 2026-09-10 JST — Escalation handoff invariant hardening

- `EscalationDispatchRequest`のdecision／target整合性を検証し、same provider、other provider、higher tier以外のhandoffを拒否する。SQLiteでも`escalation.dispatch_ready` eventがsnapshot経由で永続化されることを確認した。
- Phase 7 focused testsは`18 passed`、全回帰は`375 passed, 1 skipped`。G6O1と既存Gate statusは変更していない。

### 2026-09-10 JST — Reviewed escalation dispatch handoff

- `EvaluationCoordinator.prepare_dispatch()`を追加し、accepted reviewとplan identityを検証した受理済みplanだけを、Provider選択・予算再確認・Task mutation・実dispatchを行わない不変`EscalationDispatchRequest`へ変換する。`escalation.dispatch_ready` eventにはplan、reviewer、approval referenceをdurableに記録する。
- rejected review、別planのreview、terminal evaluationからのhandoffは拒否する。Phase 7D focused testsは`23 passed`、全回帰は`373 passed, 1 skipped`。
- `3bb5ba6`のexact-head GitHub Actionsは`v2-core` run `34407852066`（Python 3.10/3.11 success）と`v2 tests` run `34407851906`（success）を確認した。G6O1と既存Gate statusは変更していない。

### 2026-09-10 JST — Explicit escalation plan review boundary

- `EscalationPlan`へ`plan_id`を付与し、`EvaluationCoordinator.review_plan()`でhost/operatorのaccepted／rejectedをdurable eventへ記録する境界を追加した。actor、approval reference、reject reason、対象planを保存するが、Provider dispatch、Task mutation、Worker artifact適用は行わない。
- plan reviewの対象は同じ`EvaluationCycle`が生成した`escalation.planned`に限定し、plan event identityとevaluation resultの一致を検証する。対象テストは`14 passed`、全回帰は`371 passed, 1 skipped`。
- `d1372b8`の実装を反映したCurrent State／Phase計画／Multi-Free要件／Traceabilityを同期した。G6O1と既存Gate statusは変更していない。

### 2026-09-10 JST — Bounded tier routing and host-verified DevFarm handoff

- Phase 7A/Bの`IntelligenceRoutePolicy`を明示opt-inのresource tier routingへ接続した。`RouteRequest`は許可tierとresource metadataをexact matchし、通常routing、Task metadataによる自己昇格、Providerの自動activationは変更しない。
- Phase 7Dの`EvaluationCoordinator`はhost側のevidenceをdurableに記録してから`escalation.planned`を記録し、有限planを返す。planの自動dispatch、Task mutation、model自身のtier昇格は行わない。
- OpenRouter `openrouter-worker-smoke-005`で、valid unified diffを専用worktreeへ限定適用し、manifest-approved host test `1 passed`を確認した。結果は`.devfarm/`のhandoff artifactに保持し、公式branchへ統合していない。Cloudflare `cloudflare-worker-smoke-007`はresponse decode failureでproposal未生成である。
- `scripts/devfarm_worker.py`はmodelの非文字列notesを明示的なJSON textへ正規化し、欠落した`notes.md`をhost側で補完する。modelのtests claimは正式証拠に採用しない。
- `MISTRAL_API_KEY`を再読込みしたlive attemptは推論HTTP 429で未資格化。証跡を`spec/v2/evidence/phase7-mistral-2026-09-10.json`へ保存した。G6O1と既存Gate statusは変更していない。
- 最新ローカルv2全回帰は`368 passed, 1 skipped in 68.74s`。Windows ACL skipはdeployment-ownedのまま。

### 2026-09-10 JST — Phase 7D bounded evaluation coordination

- `EvaluationCoordinator`を追加し、host側の`EvaluationEvidence`を既存のdurable Eventへ記録してから、`RETRY_SAME`／`RETRY_OTHER_PROVIDER`／`ESCALATE`だけに有限な`EscalationPlan`を返す境界を実装した。PASS／WAIT_HUMAN／FAILは自動dispatchへ変換しない。
- evaluatorの判定、escalation context、task/attempt identityの一致を検証し、Provider dispatch、Task mutation、model自身のtier昇格はこのsliceへ持ち込んでいない。targeted regressionは`18 passed`。

### 2026-09-10 JST — Refactor Freeze and exact-head verification

- R2〜R6の責務分離を完了した。ControllerのModel turn／legacy direct-provider executor、ResourceLedgerのBudgetReservationStore、SQLiteStateStoreのschema／core／effect repository、ToolRuntimeのexecutor／effect guard、ProviderRegistryを専用moduleへ分離し、公開API・transaction owner・schema v7・timeout／cancellation semanticsを維持した。
- Dispatcherのrouting／budget Snapshot読出しをResourceControlPlaneへ閉じ、内部Ledger／Router／Governorへの層越えを除去した。
- DevFarmとResourceの巨大テストを責務別へ分割した。`test_devfarm_manifest.py`、`test_devfarm_patch_validation.py`、`test_resource_migrations.py`、`test_resource_observations.py`、`test_budget_reservations.py`を追加し、`359 passed, 1 skipped`を維持した。
- R8 import smoke（主要12 module）`566ms`、`compileall src recovery scripts`、旧v1トップレベルimport監査を確認した。v1実行資産は`legacy/v1-final`に隔離済みである。
- `v2-core`をPython 3.10/3.11 matrixへ統合し、重複full suiteとcollect-onlyを除去した。`47191d4`のexact-head CIは`v2-core` run `34384890829`（3.10/3.11 success）と`v2 tests` run `34384890828`（success）である。
- Cloudflare／OpenRouterのDevFarm実Worker試行はAPI到達後にstrict unified-diff検証で拒否された。host-verified Worker成功や自動統合は記録せず、G6O1および既存Gate statusは変更していない。

### 2026-09-10 JST — Refactor state and DevFarm evidence synchronization

- ResourceLedgerの同一SQLite transaction境界を維持したまま、Resource Catalog、Resource Observation、Quota Observation、Provider Healthの内部storeを分離した。公開Facade、schema、budget semanticsは変更していない。
- Phase 7Cにhost側の決定的 `TaskEvaluator` と durable `evaluation.recorded` eventを追加した。Model自身のtests claimや自己評価は正式な成功証拠にせず、有限なPASS／RETRY／ESCALATE／WAIT_HUMAN／FAIL判定だけを記録する。
- DevFarmのWorker proposalはCloudflare／OpenRouterともAPI到達後にstrict unified-diff validationで拒否された。host-verified test、公式branch統合、実Worker成功とは扱っていない。
- `MISTRAL_API_KEY` を環境から再読込みしてlive qualificationを試行したが、推論HTTP 429で未資格化。証跡を `spec/v2/evidence/phase6-mistral-2026-09-09.json` に保存した。G6O1とProvider Gate判定は変更していない。
- 現在状態の正本を `docs/CURRENT_STATE.md` に追加し、README／Phase 6計画／DevFarm説明／Traceabilityの古いテスト件数、credential状態、実装所有者、外部送信表現を同期した。
- 文書同期commit `30a1cd3` のGitHub Actions exact-head CIを外部確認し、`v2-core` run `34374695007` と `v2 tests` run `34374695021` がsuccessであることを記録した。これは同commitの外部観測であり、後続commitのCI成功を意味しない。

### 2026-09-09 JST — Shared compatible HTTP, OpenRouter qualification, and quota units

- Groq／SambaNovaの重複HTTP実装を共通 `OpenAICompatibleHttpTransport`／`OpenAICompatibleHttpProvider` へ集約し、Mistral／OpenRouterを同じ境界へ追加した。Mistral固有の `max_tokens` 差分はAdapter内に限定し、Provider SDK型をKernelへ漏らさない。
- 共通HTTP層にsafe error decoderと `/models` 読出しを追加した。Groqの `/v1/models` probeはHTTP 403（permission診断）で、推論成功やmodel permissionを推測していない。
- OpenRouter `openrouter/free` をcanonical Controller -> ProviderDispatcher -> ProviderRegistry経路で実通信qualificationし、ToolCall／ToolResult／final、durable audit、budget reconciliationを確認した。quota残量は未報告のためunknownのまま。
- ResourceLedgerをschema v7へordered migrationし、`unit`（requests／tokens／neurons）、generic limit／remaining／consumed、`authority`を追加した。Cloudflareの既知モデルについてtoken usageからのNeuron消費推定は`authority=estimated`・低confidenceで保持し、残量観測とは分離した。
- Provider health記録をResourceControlPlane APIへ閉じ、DispatcherがRouter内部のLedgerへ直接到達しない依存方向へ修正した。
- Provider intent／replay／durable auditの責務を `ProviderDispatchJournal` へ分離し、Dispatcherは選択・実行・ControlPlane連携に集中するFacade境界へ整理した。
- `test_phase6_integration.py`をbudget、provider dispatch、provider reconciliation、provider fencing、terminal stateの5ファイルへ責務別に分割し、Gate／Traceabilityのテスト参照を同期した。
- ローカルv2全回帰は `316 passed, 1 skipped`。同一timestampのquota観測は挿入順をtie-breakerとして最新値を決定する。Mistralは資格情報未設定、GroqはHTTP 403、SambaNovaはHTTP 429/402のため、いずれもlive無料Providerの成功とは扱っていない。G6O1はBLOCKED_EXTERNALのまま。実装baseline `8c8176b783dcb145065de9116958b0192620755c` のexact-head CIは `v2-core` run `34357779353` と `v2 tests` run `34357779324` がsuccess。
- `ResourceReadView`、`ProviderHealthStore`、`LegacyDirectProviderJournal`を追加し、Routerの読み取り、Provider health、Controller compatibility経路の永続化責務を内部分離した。Controller／Providerの公開契約、SQLite schema、Gate判定は変更していない。ローカルv2全回帰は `317 passed, 1 skipped`。実装baseline `9cfac134a1e34c2228acde75ba5b49c65111a57a` のexact-head CIは `v2-core` run `34360260517` と `v2 tests` run `34360260443` がsuccess。
- `ProviderDefinition → ProviderFactory → ProviderRegistry`を追加し、Provider固有の構築配線と資格情報解決を分離した。開発Worker Runnerはmanifest-scoped入力、result／patch／tests／notes artifact、範囲外変更拒否を提供する。自動activation・無承認送信・自動patch適用は行わず、明示実行時は承認済みoutbound scopeだけを外部Providerへ送信する。

### 2026-09-09 JST — Free Provider HTTP boundary and development Worker Farm

- Groq Chat CompletionsとCloudflare Workers AI RESTのopt-in HTTP Adapterを追加し、endpoint／認証／Provider固有envelopeをAdapter内へ閉じ込めた。Groqのrate-limit headerは正規化 `usage.quota_observation` として返し、存在しないCloudflare quota値は推測しない。
- `scripts/qualify_free_provider.py` はcanonical Controller -> ProviderDispatcher -> ProviderRegistry経路を一時SQLiteで検証し、資格情報未設定時は`blocked_external`として終了する。live成功やGate昇格を推測しない。
- `.devfarm/`をGit ignore対象とし、`scripts/devfarm.py`のmanifest ownership検証、result contract、workerごとのGit worktree準備を追加。正式runtimeのScheduler／AgentBackend／Phase 7 Multi-Agentとは分離した。
- Controllerがassistant tool-call turnをcheckpoint可能な会話履歴へ保持し、Groq等のOpenAI互換wire contractでToolResult直前に再送できるようにした。資格確認スクリプトのcanonical Dispatcher往復もモック回帰で固定した。
- Worker Farmにmanifest/resultの正規化検証CLIを追加し、Codexのreview前にbase revision、保護領域、変更範囲を再検証できるようにした。
- Provider qualificationの実行方法と、資格情報・result artifact・Gate昇格を分離する運用境界を`docs/DEVFARM.md`へ追記した。
- 現行READMEと要件IndexのProvider quota観測状態を、実装済みGroq header正規化と未実装Providerの後段要件に同期した。
- Adapter decoder、資格情報fail-closed、manifest ownership、保護領域拒否、Provider transcript、qualification pathの回帰を含むローカル全回帰は `288 passed, 1 skipped`。Groq／Cloudflareのlive qualificationは資格情報待ちで未取得。
- `22c2654` を実装基準として、GitHub Actionsのexact-head CI（`v2-core` run `34327300092`、`v2 tests` run `34327300120`）がsuccessであることを外部確認した。`GATE_STATUS.json.evidence_head` はcode-baseline-onlyのまま維持し、後続の文書同期commitが自分自身を証明する構造は採らない。
- Current Stateを `22c2654` の実装内容とCI観測へ同期した。資格情報未設定のGroq／Cloudflareは引き続き`blocked_external`で、実Free Workerの起動やGate昇格は行っていない。
- v2 checkoutの探索ノイズを減らすため、旧v1 runtime、logs、memory、prompts、root v1 config、root v1 testsを削除した。v1の正本は`legacy/v1-final`、v2で必要な失敗fixtureは`tests/v2/fixtures/v1/`、v1依存は`requirements-v1-legacy.txt`に分離した。v2のrecovery diagnosticsは`config/v2.yaml`のみを確認する。
- ResourceRouterにresource/quota観測を一括取得するRoutingSnapshotを追加し、同一判断内のquota domain別DB読出しとProviderDispatcherのSurvival用resource再読出しを削減した。選択条件とshared quotaの保守的な最新観測semanticsは維持している。
- 明示的に再読込した資格情報でCloudflare Workers AIのcanonical Phase 6 live qualificationを実行し、ToolCall／ToolResult／final response、durable audit、budget reconciliationを確認した。quotaは応答未報告のため`unknown_not_reported`。GroqはHTTP 403で未 qualification、OpenRouterは現状live HTTP未実装のまま維持した。

### 2026-09-09 JST — Phase 6B/C quota operation and Phase 7A/B policy seam

- 同一 `quota_domain` の複数Credentialを加算せず、fresh observationの最小headroomとしてRouterへ適用。`concurrency_limit` をdispatch前のhard filterにした。
- Provider responseの正規化済み `usage.quota_observation` だけをResourceLedgerへ取り込み、壊れた補助telemetryは有効なmodel resultを失敗扱いにしない。Provider固有header解析はAdapter側の後段作業として維持。
- `TaskType`、`RiskLevel`、`required_capabilities` をTask JSONへ後方互換に追加し、`TaskIntelligencePolicy` がL0〜L3のbounded minimum/maximumを決定する。model metadataによる自己昇格は参照しない。
- ControllerはTask profileをModelRequestの要求capabilityとpolicy metadataへ渡すが、実Providerのmodel selectionは変更していない。Evaluator、escalation、AgentBackend/MCPは後段。
- ローカル全回帰は `277 passed, 1 skipped`。直前のquota基盤commit `fb793fe5b57f1b6ce04e54e6cc6af05ff676742b` のGitHub Actions exact-head CIは `v2-core` run `34321435614` / `v2 tests` run `34321435595` がsuccess。G6O1は引き続き`BLOCKED_EXTERNAL`。

### 2026-09-09 JST — Phase 6A quota-aware Multi-Free provider foundation

- ResourceLedgerをschema v6へ拡張し、`quota_domain`、durable quota observation、quota remaining/reset、latency/failure EWMA、inflight/concurrencyの観測値をordered migrationで保持できるようにした。
- quota domainを宣言したResourceはmissing/future/stale observationをfail-closedで除外し、fresh quota headroomを優先してRouterが選択する。ProviderDispatcherの通常経路とControllerのcompatibility/legacy direct経路は変更していない。
- Groq、Cloudflare Workers AI、Mistral、OpenRouter FreeのAdapter境界を、注入transportと既存のnormalized ModelProvider contractで追加した。CI/local testは実通信を行わず、live qualificationやquota header自動取得は未実施。
- G6O1は実paid Providerのworst-case qualificationとdeployment-owned protected budget config待ちの`BLOCKED_EXTERNAL`を維持し、Phase 7は開始していない。ローカル全回帰は `267 passed, 1 skipped`。コード基準 `0d690d888b58574b721572b81c16de20ee324066` に対するGitHub Actions exact-head CIは `v2-core` run `34318901211` / `v2 tests` run `34318901190` がsuccess。

### 2026-09-09 JST — Multi-Free Provider migration preparation

- 添付要件を docs/requirements/ の7章とIndexへ分割し、必要章だけを参照できる導線を追加。将来要件は現行Gateの達成として扱わない。
- 通常のProvider経路を Controller -> ProviderDispatcher -> ProviderRegistry -> concrete Provider と明示し、Controllerのdirect Provider分岐をcompatibility/legacy pathとして文書化。
- 予算・資源制御に関する既存Integration Testの3件を tests/v2/test_budget_dispatch.py へ移し、Provider経路の境界テストを tests/v2/test_provider_dispatch.py へ追加。全回帰は 253 passed, 1 skipped。
- G6O1の BLOCKED_EXTERNAL、Phase 6 foundationとG6O2〜G6O6の判定、da74b3bのtested code baseline / 74e54f9のdocumentation-evidenceモデルは維持。

### 2026-09-09 JST — Phase 6 operational hardening continued

- Dispatcherの明示 `task_id` 互換入口と公開モジュール境界を追加し、Registry不整合時の予約リークを防止。
- 実測請求が保護予算を超えた場合の予約を `unknown` として保持し、監査・照合なしの解放を禁止。
- Schedulerのmaintenance判定をclaimトランザクション内へ移し、待機Taskを `waiting` にparkして明示 `wake()` まで再実行しない契約を追加。
- Provider timeout/transport後の `resume()` 再送と、照合待ちTaskのterminal cancellationを抑止。
- Dispatcher所有のtransport失敗も `WAITING_RECONCILIATION` へ統一し、Provider cancellation後の再送を抑止。
- ResourceLedgerのruntime maintenance fenceを接続間で共有し、予約トランザクション内でも再確認。
- 現行 `v2/bootstrap` のローカル全テストは `252 passed, 1 skipped`（Windows ACL依存テスト）。外部GitHub Actionsは、コード・テストcommit `da74b3b` に対して `v2-core` run `34311452342` / `v2 tests` run `34311452341` がともにsuccess（各runの `GITHUB_SHA` は対象commitと一致）。証跡記録commitはコード基準を変更せず、CI providerをexact-headの正本とする。
- Phase 6のG6O3/G6O4/G6O5を各責務のローカル受入でVERIFIEDへ再分類し、実Ollama Dispatcher経路と隔離Recovery operator drillを追加。Phase 6全体は有償Providerのworst-case費用実証待ちでIN_PROGRESSを維持。
- Workerのlease contextをper-run immutable `ExecutionContext`へ移し、Recovery Reserveの直接 `recovery=True` を拒否して `BudgetAuthority.reserve_recovery()` に限定。後者は永続Taskの `task_class="recovery"` を要求する。
- resource-ledgerのnative reservation整合性をRecovery validatorで検査。
- Phase 6 operational Gateは引き続き `IN_PROGRESS`。G6O2〜G6O6は各責務の受入をVERIFIED済みで、G6O1のみ実Providerのpaid worst-case dispatchと保護operator budget設定の外部実証待ち。
- Protected budget config loaderがsymlink、型の暗黙変換、POSIXのgroup/world書込を拒否し、Phase 6のlocal qualificationも外部設定経路を通すよう同期。
- 有償Provider qualification用のfail-closed入口を追加。明示的なbilling確認がない実通信を拒否し、Providerが実コストを返さない場合は成功扱いせず照合待ちにする。
- 有償予約の`usage.cost_minor`欠落をDispatcher成功へ通さないよう修正。無料固定価格リソースだけは互換上0へreconcileし、有償予約はunknown／照合待ちに固定。
- 外部ProviderのHTTP 5xx／408／status不明エラーを、送信後の不確実な結果として照合待ちへ統一。Workerはheartbeat障害をterminal failureへ誤変換せず、leaseが有効なら有限retryへ戻す。deadlineとcancel要求の競合も`unable_to_confirm`へ記録する。
- ProviderDispatcher経路でも`ProviderError.requires_reconciliation`を予算状態へ伝播し、HTTP 5xx／408／status不明時の予約を`confirmed_no_charge`へ誤解放しない回帰テストを追加。

### 現在の到達点（2026-09-08 JST）

- Phase 6A〜6Eを実装。Native-unit Resource Ledger、fail-closed Budget Governor、privacy-first Router、NORMAL / CONSERVE / SURVIVAL、独立Recovery Operator、durable lease queue、Controller dispatch reservationを追加し、Stage Fへ証拠を登録した。

- v2 の全テストが `131 passed`（2026-09-08 JST のローカル実行）。
- 外部副作用の曖昧状態を `waiting_reconciliation` としてタスクに永続化し、照合確定後の再開を統合テストで検証（全64件）。
- Windows の実 symlink を使った workspace 外逸脱拒否テストが `passed`。
- Ollama `qwen3:8b` のローカル `/api/chat` とController Tool-call E2Eは検証済み。`qwen3:0.6b` の thinking traceは別の出力品質quirkとして保留。
- Gemini `gemini-2.5-flash` は実HTTPの text + model-generated ToolCall + ToolResult + final response を完走し、Phase 5の live capability matrix へ記録した。Phase 4/5のcurrent acceptanceは完了。
- `d660ec8` の GitHub Actions `v2-core` / `v2 tests` は exact-head check、pytest、JUnit artifact upload を含めて両方 `success`（run `34224800598` / `34224800634`）。
- Ollama `qwen3:8b` の実Controller E2E（ToolCall、ToolResult、final response、task completion）を確認し、D23/D24を`VERIFIED`へ昇格。`<think>` traceは既知quirkとして記録。
- A7/B14/C15/C18/C19は現行Phase 3.5 acceptanceをVERIFIEDへ再判定し、Phase 6/7の後段要件は`deferred_requirements`へ分離。Phase 6入口条件はcurrent gatesについて解禁した。
- Phase 3.5〜5の受入完了HEADは `ea575d8785f2dbbdc953a0e2b4d3ee021a83ea01`。追加された `dev_agent_codex_4docs/` はユーザー提供資料として保持し、Phase 6のcurrent evidenceはStage Fと `docs/PHASE6_PLAN.md`で管理する。

### 2026-09-08

- 継続 hardening: 外部副作用の dispatch 後 timeout / connection failure /
  response decode / output limit / output schema failure を
  `reconciliation_required` + `cause` に統一し、Controller が
  `WAITING_RECONCILIATION` へ遷移する経路を追加。
- Tool の実効引数を canonical operation identity と実行処理で共有し、
  相対 path / 絶対 path の表現差による重複操作を防止。協調キャンセルの
  durable state に `terminated` 状態と理由を追加し、承認待ち commit crash
  復帰テストを拡張。
- `WAITING_RECONCILIATION` の commit 直後クラッシュを注入し、照合後の resume が
  外部 ToolCall を再送せず、最終 Provider 要求だけを継続することを検証。
- `generated` Tool の正常系が subprocess 境界・schema validation・結果復帰を通ることを検証。
- Event artifact を明示注入できる content-addressed store を追加。secret pattern
  拒否、root-bound read、retention purge、Controller経路のartifact refを検証。
- Provider contract harness に model-generated ToolCall、sequential ToolCall、
  normalized ToolResult、final response のoffline roundtripを追加。
- Recovery に event artifact root validator と `diagnose --artifact-root` を追加し、
  digest、byte length、metadata、missing payloadをRuntime非依存で検査可能にした。
- guarded effect中のキャンセルを `reconciliation_required` として扱い、
  cancelled commit直後のcrash復帰でcancel eventが重複しないことを検証。
- cancellation後にguarded effectの結果を確認できない場合、checkpoint/eventへ
  `unable_to_confirm` を記録し、通常の `terminated` と区別する経路を追加。
- 実行中のProvider requestをcancelした場合も、threadを停止できないため
  `WAITING_RECONCILIATION` と `unable_to_confirm` を永続化する経路を追加。
- Controller の completion / failure / approval wait /
  reconciliation wait / ToolResult の critical transition を
  `commit_transition()` へ統合。Gate の状態を
  `IMPLEMENTED` / `INTEGRATED` / `VERIFIED` へ分離し、B11 と E33 の早すぎる
  `PASS` を撤回。
- untrusted / generated / process Tool の subprocess 境界、timeout 時の
  process-tree termination、協調キャンセル、イベント secret pattern 検出と
  payload byte cap を追加。
- SQLite fresh/latest schema と既存 v1→v4 ordered migration を分離し、
  Recovery に内容検証付き atomic restore、Git diagnostics、last-known-good、
  rollback plan、明示 opt-in の repair branch 操作を追加。
- Recovery に保存済み JUnit レポートの read-only 検証を追加し、v2 CI workflow
  からのレポート artifact 出力と exact HEAD check を接続。JSON state の
  atomic transition 失敗時 rollback、confirmed_failed reconciliation の
  terminal 化、dirty worktree rollback 拒否も追加。

- `feat: harden approval expiry, revocation, and immutable records`
  - SQLite / JSON 承認記録に期限 (`expires_at`) と取消 (`revoked`) を追加し、期限切れ・取消済みを fail-closed。
  - 承認 ID の重複保存を拒否し、既存監査履歴を上書きしない契約を追加。
  - 期限・取消済み承認では effect intent を生成しない厳格テストを追加。
  - 外部 intent が既に pending の場合は承認再消費より先に reconciliation_required を返し、安全な照合導線を維持。

- 作業継続（未コミット時点）: 承認待機→永続承認→`resume(approval_id=...)` の実行経路、Provider固有 call ID と内部UUIDの分離を追加。全41テスト通過後に次コミットへ確定。

- `3c4f453` `fix: use Gemini API key header authentication`
  - Gemini API キー送信を URL クエリから公式の `x-goog-api-key` ヘッダーへ変更。
  - API キーを URL やログへ露出しない境界を追加。
  - ヘッダー方式でもモデル一覧・生成が 403 になることを確認し、クエリ／ヘッダー方式だけが原因ではないと記録。
- `ab1ea10` `docs: record Gemini live probe authorization failure`
  - Gemini 403 の観測、原因候補、Google 側で確認すべき設定、再試行条件を記録。
  - 実通信を成功扱いにせず、認証・プロジェクト設定待ちとして Phase Gate に反映。
- `0b029ce` `test: classify Gemini provider failures`
  - Gemini の missing key、401/403、429、通信エラー、malformed response の分類テストを追加。
- `2714eb6` `test: close terminal checkpoint crash gaps`
  - `after_model` / `failure` checkpoint の永続化直後に停止した場合の resume を修正。
  - 終端状態を再確定し、provider や副作用処理を重複実行しないテストを追加。
  - symlink 検証済みの結果を実行計画へ反映。
- `7c59027` `feat: persist task-scoped approval records`
  - SQLite / JSON に承認記録を保存。
  - 承認を `task_id` と side-effect level にスコープし、別タスク・別用途の流用を拒否。
  - Controller から task ID を渡して承認照合する経路を追加。
- `2714eb6` で追加したクラッシュ境界、`7c59027` の承認境界、Provider failure tests を統合し、全 40 テスト通過を確認。
- `b493334` `feat: add guarded Gemini REST provider`
  - 標準ライブラリのみの `GeminiHttpProvider` を追加。
  - `GEMINI_API_KEY` をリクエスト時に読み、未設定時は fail-closed。
  - `generateContent` の `maxOutputTokens`、tool result の `functionResponse`、REST 応答の正規化を実装。
  - 実応答 fixture、payload、認証なしの安全な失敗テストを追加。
- `15c1d36` `feat: harden provider and crash recovery contracts`
  - Ollama `/api/chat` アダプタを追加。
  - Ollama の `options.num_predict` に内部の `max_output_tokens` を伝播。
  - Gemini REST 関数呼出しデコーダと sanitized fixture を追加。
  - 複数副作用 ToolCall の一部実行直後クラッシュからの idempotent resume を追加。
  - Ollama 実機で、未修正時の出力上限無視（`eval_count: 336`）を検出し、修正後 `eval_count: 16` を確認。
  - `qwen3:0.6b` が小さい上限を思考出力で使い切る事象を記録。`think:false` が環境・モデル依存で効かないため、モデル適格性試験を別 Gate とした。

### 2026-09-07〜08: v2 基盤・統合 hardening

- `5601db6` `feat: validate durable state from recovery path`
  - SQLite スキーマと task JSON を runtime import なしで検査する recovery CLI を追加。
  - GitHub Actions の v2 test workflow を追加。
- `28b99af` `feat: harden resumable kernel integration`
  - Controller checkpoint に messages、ToolResult、各種カウンタ、active step、pending ToolCall を保存。
  - resume が pending ToolCall を先に消化し、次の ModelRequest へ ToolResult の call ID / tool 名 / status を渡すよう修正。
  - Controller に StateStore を強制接続。
  - approval、idempotency、PathPolicy、failure transition を実行経路へ統合。
  - README と integration hardening 仕様を整理。
- `636dcb3` `feat: add provider adapters and contract probes`
  - Gemini transport shell、OpenAI-compatible adapter、v1 `whichOneof` failure fixture を追加。
  - Core protocol を変更せず Provider 応答を normalize する契約テストを追加。
- `7f98107` `feat: add local provider contract harness`
  - Local Provider shell、共通 normalize 処理、Provider contract harness を追加。
  - text / tool-call の offline 契約テストを追加。
- `3bc0277` `test: cover resolved symlink escape without OS privilege`
  - OS の symlink 作成権限がない場合でも、resolved path の workspace 外逸脱を検査する仮想リンク試験を追加。
- `7c98ff2` `docs: record phase 3 symlink verification caveat`
  - Windows 権限不足時の symlink 実体テスト skip と、Promotion Gate 前の再試験条件を記録。
- `833437e` `feat: add durable state graph and policy boundaries`
  - TaskGraph の depth / child 数 / cycle 制限を追加。
  - SQLite StateStore、PathPolicy、ApprovalPolicy、idempotency 永続化を追加。
  - path traversal、symlink、無許可操作、重複副作用の Phase 3 テストを追加。
- `424b472` `feat: implement v2 alpha0 deterministic kernel`
  - FakeProvider、反復型 Controller、JSON state store、Tool registry / runtime を追加。
  - Model request → ToolCall → ToolResult → final response の最小完走経路を追加。
  - step / model call / tool call の有限上限を実装。
- `de71ec3` `feat: add v2 recovery and protocol foundation`
  - Task、Step、ModelRequest、ModelResponse、ToolCall、ToolResult 等の provider-neutral protocol を追加。
  - protocol validation / serialization tests を追加。
  - 独立 Recovery の bootstrap、diagnose、state validation skeleton を追加。
- `8624638` `docs: establish v2 phase 0 baseline`
  - `spec/v2/` の requirements、behavior、data、API、implementation、test spec を追加。
  - invariants、traceability、migration matrix、ADR-001〜010 を追加。
  - `docs/V2_DEPENDENCIES.md`、`docs/V2_EXECUTION_PLAN.md`、pytest 起動設定を追加。
  - v1 を移植元ではなく failure fixture / concept archive として扱う方針を明文化。

### v2 の既知の未達・保留

- Gemini は現在のキーでモデル一覧・最小生成が HTTP 403。Google 側の API 有効化、プロジェクト、キー制限、モデル利用権限の確認が必要。
- 認証済み Provider の live contract 完走、Provider failover、quota / budget、Recovery の Git health / rollback / repair drill は未完了。
- 残りの crash boundary（pre-model、model response event 等）の追加試験が必要。
- Controller-facing の approval-wait / approval-resume 公開 API は未実装。
- qwen3 のような reasoning model について、visible response quality を含むモデル適格性 Gate が必要。
- Phase 6A〜6Eは開始・実装済み。live rollback / repair drill、automatic retry policy、generated Tool lifecycleは明示的な後段要件。

## v1 保全履歴

`legacy/v1-final` / `main` は `4dfc3b2` を保全基準とする。v1 の過去コミットは移植対象ではなく、実障害・設計判断・回帰 fixture の資料として保持する。

主な v1 履歴:

- `d5854b0`〜`cf0a39c`: 初期構築、ローカル除外、事前準備、初版。
- `567b55c`、`5d930d4`、`6df4fce`: メモリ保存、内部エラー自己改善、再帰処理基盤。
- `7388ed2`: Gemini チャットモードと履歴仕様。
- `9c795a7`〜`257bf02`: 依存整理、関数呼出し組込、main2 統合、冗長処理整理。
- `a10cb7f`: ファイル整理と Archive への隔離。
- `268c8af`: 関数処理の改善とセキュアな権限チェック。
- `1ed9dbe`: 再帰テスト済みの状態。
- `424aebe`: OpenInterpreter 検討時の構成ファイル準備。
- `ebfefad`: 再帰・内部対話の設計と実装。
- `bdd73f9`: v1 全体調整。
- `4dfc3b2`: v1 保全対象の最終 baseline。

## 変更記録の読み方

- コミット済みの実装・テスト結果と、外部設定待ちの未達を分離して記載する。
- 「確認済み」は実行したテストまたは実機プローブの結果を指し、fixture のみの確認は実通信成功とは扱わない。
- API キー、個人情報、秘密値はこのファイルへ記録しない。
