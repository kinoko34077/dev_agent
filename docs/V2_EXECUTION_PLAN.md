# dev_agent v2 実行計画

> **この文書が v2 着手中の通常の参照点です。**
>
> 詳細仕様・ADR・テストは `spec/v2/` に分離する。本書には、今どの段階にいて、次に何を行い、何を満たせば先へ進めるかだけを記録する。

## 1. 方針と現在地

- 基準仕様: 2026-09-07 の「dev_agent v2 — Codex Development Handoff」（ACCEPTED）。現在のユーザー指示がこれより優先する。
- 目的: 特定のモデルや Provider に依存せず、予算・権限・停止条件・状態を決定的に制御でき、外部から復旧できる実行 Control Plane を作る。
- 開発原則: `Mechanism-heavy / Intelligence-on-demand`。既知の反復仕事は Workflow、探索的な判断だけを Agent に任せる。
- 現在の範囲: Phase 7 integration hardening（Phase 6 foundation／operationalはG6O1を除き確定）。Codex App Server実adapter、MCP、マルチエージェント、自己修復、AI会社運営、Virtual Office UI は後続Gate。

### 2026-09-07 時点の観測

| 項目 | 結果 | 計画への影響 |
| --- | --- | --- |
| ブランチ / HEAD | `main` のみ、`4dfc3b2f9dec482b22f523ac9dcdd9fec325a36a` | このコミットを v1 保全基準にする |
| 作業ツリー | 変更なし | ブランチ作成前に退避は不要 |
| v1 の依存 | `google-generativeai`、`python-dotenv`、`pyyaml` | v2 は Provider SDK を Core に持ち込まない |
| 静的確認 | `python -m compileall -q api core function memory utils` は成功 | 構文が読めることだけを確認。動作保証ではない |
| テスト環境 | `pytest` 未導入で収集不能 | Phase 0 で再現可能な開発・テスト環境を定義する |
| 実障害証跡 | `whichOneof`、応答契約不整合、パス権限・動的 import 問題 | v1 修理ではなく v2 の回帰 fixture / 受入試験に転用する |

v1 は移植元ではなく、知見・ログ・失敗の回帰資料である。v2 は `src/dev_agent/` 以下に新設し、v1 の Runtime モジュールを import しない。

## 2. 運用方法

1. 作業開始時に本書の「現在フェーズ」「次の作業」「進行判定」を確認する。
2. 一つのフェーズ内でも、変更は独立して戻せる小さな PR / コミットに分ける。
3. 挙動を追加・変更する際は、対応する仕様 ID、受入試験、必要なら ADR を同時に更新する。
4. フェーズの Gate が全て観測可能なテストで通るまで、次フェーズの実装を始めない。
5. 不確定な技術選択は勝手に固定せず、`spec/v2/adr/` に candidate ADR を作る。
6. 本書は進捗の要約だけを更新する。詳細な要件、プロトコル、データ、テスト仕様をここへ肥大化させない。

### 現在フェーズ

**Phase 7 — bounded intelligence execution（Phase 6 foundation / G6O2〜G6O6 verified・G6O1のみ外部blocked）**

Phase 0〜5 は current acceptance verified。Phase 3.5の後段要件、Phase 4 local実Provider、Phase 5 remote実Providerの証跡は `spec/v2/GATE_STATUS.json` を正とする。Phase 6A〜6Eで resource ledger、budget reservation、privacy-first router、survival modes、独立Recovery運用、durable scheduler ownershipの基礎を実装し、Stage Fとして検証している。Stage Gでは、実Provider dispatch、通貨・period安全な予算、survival policy、lease-fenced worker、独立接続の競合証明、recovery drillを責務別に統合検証している。現在G6O2〜G6O6はVERIFIED、G6O1のみ有償worst-case実証とdeployment-owned budget設定の外部保護確認待ちである。

進行判定: Stage F foundation と Stage G operational を別々に自動判定する。G6O2〜G6O6は各責務の証拠でVERIFIED、G6O1は実paid Providerとdeployment-owned設定という外部条件でBLOCKED_EXTERNALのまま維持する。Phase 7A〜7Eは、bounded intelligence policy、thinking effort、deterministic evaluator、explicit review、EscalationExecutor、Evaluator→dispatch cycle、TaskLifecycleCoordinator、明示的な有限Lifecycle合成、workflow proposal、Gemini 3.x qualification、DevFarm host verificationを実装済みとして進める。ResourceLedger schema v9のquota block/reset policy、bounded unknown-quota admission、canonical capability projection、binding単位のprovider saturation wakeとDevFarmのRemote proposal／Host verification分離も実装済みである。最小Operation Layerの`start`／`submit`／`status`／`stop`は既存のQueue・WorkerRunner・Controller・ProviderDispatcherをcompositionして実装済みで、StateStoreとQueueは同一SQLiteを共有する。`QuotaRequalificationCoordinator`は、reset boundary後に明示された一回のProvider-neutral probe結果をfresh observationとして保存し、成功時だけdue queueをwakeする。Worker metricsにはminimum sample数・証拠期限・rollback条件を持つhard-filter限定のadvisory順位付けを追加したが、自動ResourceRouter接続は後段である。AgentBackendはthin contract、typed admission付きdispatcher、ModelProviderとAgentBackendを分けるexecution-target seamまで実装済みで、実Codex adapter、MCP、Self-Improvementは後段である。exact-head CIは `spec/v2/GATE_STATUS.json` の外部証跡方針に従う。

#### 現状の4軸整理（2026-09-12 再監査で追加）

単一の「現在フェーズ」表記だけでは、「Phase 7の実装は進んでいるが、Phase 6 Operationalの外部Gate（G6O1）は未完了」という状態を矛盾なく表現できない。以下の4軸で分けて管理する。この4軸はPhase判定を置き換えるものではなく、`spec/v2/GATE_STATUS.json`のstage別VERIFIED/BLOCKED判定が引き続き正本である。

| 軸 | 内容 |
| --- | --- |
| **Implementation Frontier**（実装が到達している範囲） | Phase 7 A〜Eのdeterministic evaluator、explicit review、EscalationExecutor、Evaluator→dispatch cycle、TaskLifecycleCoordinator、有限Lifecycle合成、workflow proposal境界、Root Planning、AgentBackend thin contract + dispatcher、development Commander、Phase 7後半A: `CodexExecBackend`（`codex exec`をbounded non-blocking subprocessとして実行する最初のconcrete adapter。exit codeのみを正とし、subprocess自身の自己申告は信用しない。temporary HOMEはreversible quarantine、認証は明示されたauth.json一枚のみ投影）と、development-only DevFarm bridge（untrackedを含むHost側Git差分検証、contained Host Verification、auto-integrationなし）まで実装済み。Phase 7後半の残り（Group Dの構造化証拠、MCP Adapter、OS Sandbox、Evidence routing接続、Workflow Promotion）は未着手 |
| **Operational Acceptance**（運用受入として確定した範囲） | Phase 0〜5、Phase 6 foundation、Phase 6 Operational G6O2〜G6O6はVERIFIED。Phase 6 Operational G6O1はBLOCKED_EXTERNALのまま未確定 |
| **External Blockers**（コード変更では閉じられない外部条件） | G6O1（実paid Providerのworst-case課金実証＋deployment-owned budget設定）、OS_SANDBOXED（filesystem/network/process/resource isolationの実証）、GitHub branch protectionのrequired status checks未設定（`gh` CLI未認証のためこの環境からは設定不可） |
| **Next Development Target**（次に着手する開発） | Codex DevFarm dogfood v0を実装・実証済み。次はGroup D（JSONL parser、structured events、usage/evidence、artifact extraction、discover/resume/reconciliation）を小さく分割して進める。MCP Adapter、OS Sandbox、Evidence routing接続、Workflow Promotionはその後の別Gate |

「Phase 7の実装を進めている」ことと「Phase 6 OperationalのG6O1が未完了」であることは矛盾しない。Implementation FrontierがPhase 7へ進んでいても、Operational AcceptanceがPhase 6で止まっている限り、G6O1が要求するcapability（実paid Provider運用）はunblockされたと扱わない。

### 2026-09-11 の現在状態

このCurrent State sliceのコード基準・検証HEADは `9689f41` である。下記のPhase 6/7履歴 baseline は当時の証跡を保持し、軽量化リファクタ後の全回帰は `625 passed, 1 skipped`（158.89秒）で確認している。push後のexact-head CIはこのHEADに対して外部観測する。

以下の詳細段落に残る過去のrevision／テスト件数は履歴証跡であり、現行の実装・検証基準は上記 `9689f41` と `625 passed, 1 skipped` である。現在状態の参照先は [`docs/CURRENT_STATE.md`](CURRENT_STATE.md) とする。

以下の次段落以降は旧baselineを含む履歴説明であり、「現行コード基準」という表現も当時の記録として読む。現在の実装基準は本節冒頭と `docs/CURRENT_STATE.md` を優先する。

次の長い履歴段落に残る`ead4dfe`はrefactor前の証拠baselineであり、現行コード基準ではない。軽量化リファクタの現行基準は`9689f41`である。

現行コード基準 `ead4dfe` では、Provider共通HTTP層、RoutingSnapshot、Resource Catalog / Observation / Quota / Health / Budget store、Phase 7A〜7Eのdeterministic evaluator・durable evidence・bounded plan coordinator・明示host review・EscalationExecutor・Evaluator→dispatch cycle・冪等なTaskLifecycleCoordinator・明示的な有限Lifecycle合成・hard-filter限定のevidence-based advisory routing・thinking effort routing、Gemini 3.x transcript/qualification、DevFarmのfail-closed manifest／result／worktree境界、Remote proposal／Host verification分離、remote／host concurrency governor、quota reset-aware queue wake／bounded requalification境界、Operation maintenance bridge、development-only Commander親Plan、ModelProviderと分離したthin AgentBackend contract、`BackendAdmission`付きAgentBackend dispatcher、ModelProviderとAgentBackendを分けるexecution-target seamを実装している。さらにOperation Resourceの非破壊検証、trusted billing/quota domain、Provider応答によるfreshness更新、DispatchDeniedの意味別Task遷移、canonical rate-limit／quota failureの`BLOCKED_QUOTA` park、append-only cancellation control、resource/binding health、domain別quota wake、Commander CAS、attempt単位immutable artifact、共有protected policy、semantic audit sanitizer、Budget rollover、WAL/busy timeoutとprocess contention testを実装している。Operationの複数binding poolはProviderFactory／ProviderRegistryへ接続し、exact current tierをhard filterした後の同Tier fallbackをboundedに行う。Commander proposalはTask manifestの固定revisionを読み、code dependencyは`INTEGRATED`後だけreleaseされる。FiniteLifecycleのcycle数はdurable historyから再構築される。通常のProvider経路は Controller -> ProviderDispatcher -> ProviderRegistry -> concrete Provider を正本とし、Controllerのdirect経路は`LegacyDirectProviderExecutor`によるcompatibility/legacyに限定する。ProviderRegistryは`providers/registry.py`、Model turnは`runtime/model_turn.py`、SQLiteのschema/core/effectは`state/`内の専用repository、Tool実行と副作用guardは`tools/executor.py`／`tools/effect_guard.py`へ分離した。DevFarmとv1実行資産は正式runtimeから独立している。最小Operation Layerは`src/dev_agent/operation.py`と`__main__.py`にあり、StateStoreとDurableQueueを同じSQLiteへ接続している。停止専用経路はprovider／resource設定に依存せず、実行中Taskにはdurable cancellation requestだけを残す。host verification済みWorkerのmetricsは`.devfarm/metrics.sqlite3`へ蓄積する。Operationの通常loopはbounded quota maintenanceを毎tick呼び、telemetryを返すOpenAI互換Providerだけが安全な`/models` probeを利用する。Commander dogfoodでは固定revision proposalからHost Verification、Codex review、明示integrationまでを決定的local harnessで実証した。evidence-based順位付けは実測をadvisoryとして使うだけで、ResourceRouterのhard constraintsを上書きしない。

以下の詳細段落には、以前の監査時点のrevision／件数を含む履歴証跡がある。現行の判断・件数・schemaは、上記の`9689f41`／`625 passed, 1 skipped`と`docs/CURRENT_STATE.md`を正本とする。

Cloudflare Workers AIは `spec/v2/evidence/phase6-cloudflare-free-2026-09-09.json` でcanonical live qualification済み。OpenRouter Freeも `spec/v2/evidence/phase6-openrouter-free-2026-09-09.json` で同経路のToolCall往復、durable audit、budget reconciliationまで確認済みだが、quota残量は未報告である。Gemini 3.5 Flash-Liteと3.8 Flashは `spec/v2/evidence/gemini-3.5-flash-lite-qualification.json` / `gemini-3.8-flash-qualification.json` でthoughtSignature roundtripを含むcanonical qualification済みである。Groqはmodels endpoint HTTP 403、SambaNovaは推論HTTP 429/402、Mistralはキー読込み後の推論HTTP 429で、いずれも未 qualificationのまま維持する。ResourceLedger schema v8はquotaのmetric／window／reset／blocked stateをordered migrationで保持し、typed ProviderErrorから保守的blockへ接続する。最小Operation Layerの`start`／`submit`／`status`／`stop`は既存のDurableQueue・WorkerRunner・Controller・ProviderDispatcherをcompositionし、StateStoreとQueueは同一SQLiteを共有する。停止専用経路はprovider／resource設定に依存せず、実行中Taskへはdurable cancellation requestを残す。直近のローカル全回帰は `563 passed, 1 skipped`（115.54秒）である。GeminiのDevFarm `gemini-worker-phase7-003` と、独立所有ファイルの`gemini-worker-parallel-a` / `gemini-worker-parallel-b` はhost test `7 passed`である。Evaluator→dispatch cycle focusedは`18 passed`、finite lifecycle focusedは`11 passed`、Commander focusedは`4 passed`、Evidence routing focusedは`5 passed`、AgentBackend focusedは`23 passed`である。今回のexecution target／Operation multi-provider focusedも追加で確認した。Commander dogfood `phase7-commander-local-dogfood-004`は決定的local harnessでHost Verification `1 passed`とCodex明示integrationを完了したが、外部Cloud Worker成功とは扱わない。現行loopのbounded maintenanceはprovider-neutral callbackを利用し、telemetryを返さないProviderのquota blockは解除しない。現行コード基準のexact-head CIはGitHub Actionsで外部観測し、repo内Gateへ自己記録しない。G6O1と既存Gate判定は変更していない。

CloudflareのOperation Layer external E2Eは `spec/v2/evidence/phase7-operation-cloudflare-2026-09-10.json` に保存し、実Provider経路のsubmit／start／ToolCall／ToolResult／final／durable auditを確認した。これはG6O1やGate statusの昇格証拠ではない。

Phase 7統合acceptanceは、Commander固定revision／`INTEGRATED` dependency、FiniteLifecycle restart、quota reset後のbounded probe／wake、Cloudflare Operation external E2E、Commander dogfoodまで確認済みである。Evidence routingは実Provider Worker metricsのminimum sample、freshness、rollback条件が揃うまで`DEFERRED_ADVISORY`とし、実AgentBackend adapter／MCPはその後の別Gateへ送る。

Phase 7後段の最初のsliceとして、`src/dev_agent/backends/protocol.py`にModelProviderとは別のthin AgentBackend contractを追加した。これはidentity／session／event／cancellation／resultの表現だけを担い、Codex等の実接続、MCP、既存Control Planeの置換はまだ行わない。

続くdispatch sliceでは、`AgentBackendDispatcher`が既存のeffect intentをdispatch identityとして利用し、session／event／result／cancel／explicit reconciliationをdurable Eventと既存reconciliationへ接続した。`BackendAdmission`によるstrict authorizationとTask／Backend capability coverage、`ExecutionTargetPolicy`によるModelProvider／AgentBackendの選択分離も追加した。Fake Backendでrestart、重複抑止、UNKNOWN、sequence conflict、reconciliationを検証したが、Codex App Serverの実transportやHost Verification E2Eは未実装である。

## 3. フェーズ別ロードマップ

| Phase | 目的 / 主な成果物 | Gate（次へ進む条件） | 対応マイルストーン |
| --- | --- | --- | --- |
| 0. Baseline・仕様基盤 | `legacy/v1-final`、`v2/bootstrap`、v1 資産棚卸し、`spec/v2/`、追跡表、ADR-001〜010 | baseline を再現でき、v2 の不変条件・最初の受入試験・保留事項が文書化済み | local branch 完了、remote publication 未確認 |
| 1. Recovery / Protocol | 独立 Recovery skeleton、Task / Step / ModelRequest / ModelResponse / ToolCall / ToolResult の型と直列化 | Provider 非依存の型検証・直列化・診断 CLI がネットワークなしで通る | protocol 完了、Recovery は skeleton |
| 2. 最小決定的 Kernel | FakeProvider、単一 Task の反復 Controller、イベント / checkpoint、無害な Tool registry | Model request → ToolCall → ToolResult → final response → completed が全履歴付きで通る。上限超過と不正応答が定義済み失敗になる | 完了（`v2-kernel-alpha0` 相当） |
| 3. Task・Policy・永続化 | Task Graph、DAG/cycle/depth 制限、SQLite resume、正規化パス、権限 / approval、idempotency | 強制終了後 resume、cycle / traversal / symlink / 無許可操作 / 重複副作用を試験で防止できる | primitive 完了、Controller integration hardening 中 |
| 3.5 Kernel integration hardening | checkpoint execution state、ToolResult protocol、policy/idempotency integration、failure transition | integration Gate と crash injection を満たす | current acceptance 完了。後段要件は Phase 6/7へ分離 |
| 4. ローカル実行基盤 | Local Provider adapter、provider contract harness、v1 ログ / 入出力 fixture の整備 | クラウドなしで実 Local Provider が代表タスクを完了する | Ollama `qwen3:8b` real text/tool E2E verified |
| 5. Provider 多重化 | Gemini adapter、新しい独立 Provider、ライブ Contract Probe、capability matrix | Provider の追加で Core を変更せず、実 response を normalize し live probe を記録する | Gemini `gemini-2.5-flash` real text/tool E2E verified |
| 6. 資源・生存・外部復旧 | 資源 ledger、budget governor、NORMAL / CONSERVE / SURVIVAL、Rescue CLI / MCP、復旧 drill | 支払上限を呼出前に遮断し、通常 router を壊しても外部 Agent が診断・修復できる | `v2-survival-alpha` |
| 7. 安全な拡張 | bounded intelligence policy、deterministic evaluator、reviewed escalation dispatch、Evaluator→dispatch cycle、Gemini 3.x qualification、host-verified DevFarm、Workflow proposal | Task lifecycleへ有限に接続し、実績ベースrouting・workflow promotion・rollback可能な候補を証明する。AgentBackend/MCPは別Gate | Phase 7A〜7Eの基盤と一回cycle完了、後段実装中 |
| 8. 複数役割・事業検証 | manifest-defined roles、bounded handoff、AI Company benchmark、収益 ledger / 再投資規則 | 一 Provider 停止下で、実タスクの artifact・検証・状態保存・approval handoff が完了する | 統合検証 |
| 9. Virtual Office UI | 運用状態、approval、audit、resource を表示する UI | Runtime の正式 API のみを用い、制御・監査・復旧を妨げない | 最終 UI |

## 4. Phase 0 の実行チェックリスト

- [x] 現在の `main@4dfc3b2` を指す `legacy/v1-final` を作成し、v1 を通常変更対象から外す。
- [x] 同じ baseline から `v2/bootstrap` を作成する。
- [x] `spec/v2/` に `INVARIANTS.md`、requirements / behavior / data / API / implementation / test spec の骨格を置く。
- [x] 各不変条件に ID（例: `INV-001`）を与え、要件 → 実装 → 試験の追跡表を作る。
- [x] `MIGRATION_MATRIX.md` に v1 資産を「fixture / concept only / archive / no import」で分類する。
- [x] v1 failure fixture候補を秘密情報なしで `tests/v2/fixtures/v1/` へ抽出し、残りのlogs/memoryは `legacy/v1-final` に隔離する。
- [x] Python バージョン、依存、テスト起動方法を決定し、ローカルで test collection を再現可能にする。
- [x] ADR-001〜010 の accepted decision を短く正文化する。

Phase 1〜2 の追加 Gate:

- [x] Provider-neutral protocol の round-trip / invalid input tests。
- [x] 外部 Provider なしの Recovery diagnostics。
- [x] FakeProvider の ToolCall → ToolResult → final response 成功経路。
- [x] `max_steps` / `max_model_calls` / `max_tool_calls` の有限停止。
- [x] Tool registry 経由の schema / enabled 検証と Event / checkpoint trace。
- [x] SQLite の再オープン後に Task を読み込み、checkpoint の pending ToolCall から Controller を `resume()` できる。
- [x] TaskGraph の depth / child 数 / cycle 検証。
- [x] canonical path、workspace 外逸脱、symlink 先の deny-by-default policy を ToolRuntime 経路で確認。
- [x] SQLite idempotency key による副作用の重複防止を Controller 経路で強制。

Phase 4〜5 の追加 Gate:

- [x] Local Provider shell が cloud なしで text / tool-call contract を通る。
- [x] v1 `whichOneof` failure fixture を adapter 境界で分類できる。
- [x] Gemini transport shell と独立 OpenAI-compatible shell が同じ Core protocol を返す。
- [x] 認証済み実 Provider の live Contract Probe と capability matrix（Ollama / Gemini の observed evidenceを記録済み）。

検証済み: Windows の symlink 作成権限を有効化した環境で、実 symlink の workspace 外逸脱拒否テストが `passed` になった。権限のない環境では同じテストが安全に skip されるため、Promotion Gate では権限付き実行結果を証跡として要求する。

## 5. alpha0 の最小スコープ

alpha0 は「賢い Agent」ではない。決定的な Controller が、FakeProvider から返る一つの ToolCall を安全に処理できることを示す。

```text
create Task
  → persist task/checkpoint
  → FakeProvider(ModelRequest)
  → validate ToolCall and policy
  → execute one harmless registered tool
  → persist ToolResult/event
  → FakeProvider(final ModelResponse)
  → mark completed
```

最低受入試験:

- 成功経路の Task、Step、イベント、artifact reference が追跡できる。
- `max_steps` などの有限上限で必ず停止する。
- malformed FakeProvider response は `provider_decode` または `schema_validation` の正規化失敗になる。
- 再起動後に保存済み Task / Step / event を検査できる。
- `src/dev_agent/` は旧 `api/`、`core/`、`memory/`、`function/` の Runtime を import しない。

## 6. 常に守る境界

- Controller が終了、retry、子タスク数、予算、権限、approval、状態遷移を所有する。LLM はそれらを決めない。
- 実行ループは反復型であり、main / agent Runtime への再帰呼出しを行わない。
- Provider の生レスポンスは adapter 内で正規化し、Core には typed protocol だけを渡す。
- すべての外部費用は Hard Budget と Recovery Reserve の内側に収める。初期の追加外部費用上限は月額 JPY 2,000。
- 新規有料契約、支払い、credential 変更、外部公開、不可逆削除、保護方針の変更、高リスク自己変更の main merge は human approval を要する。
- Rescue subsystem は通常 Runtime、model router、agent framework、vector DB に依存しない。

## 7. 着手しないものと判断が必要なもの

次の選択は対応フェーズまで保留する: local model、追加 Provider、LangGraph / Pydantic AI 等の採用、SQLite 以降の永続基盤、router scoring、recovery reserve の率、AI Company の事業モデル、UI framework。

特に framework は「導入したい」だけでは採用しない。必要性、代替案、exit strategy、再検討条件を ADR に記録してから導入する。

## 8. main への昇格条件

`v2/bootstrap` を `main` に昇格するのは、少なくとも Fake / local / cloud の各 Provider 完走、Provider failover、step/depth/cycle 上限、kill-resume、権限とパストラバーサル / symlink、malformed response、quota / timeout / 429、v1 replay、Hard Budget / reserve、Rescue CLI、外部 Agent + Rescue MCP 復旧 drill、そして v1 Runtime 非 import を全て実証してからとする。

## 9. 変更履歴

| 日付 | 内容 | 状態 |
| --- | --- | --- |
| 2026-09-07 | 初版。v1 現状観測と accepted handoff を基に、日常参照用の段階計画として整理 | accepted baseline |
