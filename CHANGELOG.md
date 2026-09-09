# Changelog

このファイルは、`dev_agent` の v1 保全と v2 再構築について、チャット上で確認した方針・実施結果と、リポジトリに確定した変更を時系列で記録する。

## [Unreleased] — v2/bootstrap

### 2026-09-09 JST — Phase 6 operational hardening continued

- Dispatcherの明示 `task_id` 互換入口と公開モジュール境界を追加し、Registry不整合時の予約リークを防止。
- 実測請求が保護予算を超えた場合の予約を `unknown` として保持し、監査・照合なしの解放を禁止。
- Schedulerのmaintenance判定をclaimトランザクション内へ移し、待機Taskを `waiting` にparkして明示 `wake()` まで再実行しない契約を追加。
- Provider timeout/transport後の `resume()` 再送と、照合待ちTaskのterminal cancellationを抑止。
- Dispatcher所有のtransport失敗も `WAITING_RECONCILIATION` へ統一し、Provider cancellation後の再送を抑止。
- ResourceLedgerのruntime maintenance fenceを接続間で共有し、予約トランザクション内でも再確認。
- 現行 `v2/bootstrap` のローカル全v2テストは `233 passed`。外部GitHub Actionsも `e4464c8` に対して `v2-core` run `34303157036` / `v2 tests` run `34303157026` がともにsuccess（各runの `GITHUB_SHA` は対象commitと一致）。
- Phase 6のG6O3/G6O4/G6O5を各責務のローカル受入でVERIFIEDへ再分類し、実Ollama Dispatcher経路と隔離Recovery operator drillを追加。Phase 6全体は有償Providerのworst-case費用実証待ちでIN_PROGRESSを維持。
- Workerのlease contextをper-run immutable `ExecutionContext`へ移し、Recovery Reserveの直接 `recovery=True` を拒否して `BudgetAuthority.reserve_recovery(task_class="recovery")` に限定。
- resource-ledgerのnative reservation整合性をRecovery validatorで検査。
- Phase 6 operational Gateは引き続き `IN_PROGRESS`。実Providerのpaid dispatch、独立queue/state authority、operator recovery drill、exact-head CIの最新証跡を継続取得する。

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
