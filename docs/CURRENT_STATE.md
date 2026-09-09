# Current State — v2/bootstrap

現在のコード基準は `b6e2892a8729dbef453d28161d98def2ea63383d` です。R2〜R7の
リファクタを完了し、公開Protocol、schema v7、Provider contract、Gate判定は
変更していません。GATE_STATUSのstatusはこの同期でも変更しません。

## 判定

- Phase 6 foundation: `VERIFIED`
- Phase 6 operational: `G6O2`〜`G6O6` は `VERIFIED`
- `G6O1`: `BLOCKED_EXTERNAL`（実paid Providerのworst-case課金実証と、deployment-owned budget設定の外部保護が必要）
- Phase 7A/B/C/D: Task profile、bounded policy、決定的host evaluator、durable evidence、Evaluatorから有限なescalation planを返すcoordinatorまで実装済み。実Model tier routing、planのdispatch実行、AgentBackend、MCPは未実装
- Gate昇格やlive qualificationの成功は、local testやWorker proposalから推測しません

## 検証

- v2ローカル全回帰: `359 passed, 1 skipped in 67.97s`
- Phase 7D coordinator targeted regression: `18 passed in 0.30s`
- skip: `tests/v2/test_budget_reservations.py:142`（Windows ACLはdeployment-owned）
- 変更前refactor baseline: `8bf7c2e`、`358 passed, 1 skipped in 66.76s`
- exact-head GitHub Actions: `47191d4` に対し `v2-core` run `34384890829`（kernel 3.10 job `102578562036`、3.11 job `102578562331`）と `v2 tests` run `34384890828` がsuccess
- `v2-core` はPython 3.10/3.11 matrixでfull `tests/v2`、3.11のみcompileallを実行し、`v2 tests`は互換provider smokeを担います。重複full suiteとcollect-only実行は除去しました
- import smoke: 主要runtime/resource/state/tool/provider/recovery/devfarm 12モジュールを `566ms` でimport、`compileall src recovery scripts` 成功

## Provider状態

| Provider | 状態 | 備考 |
| --- | --- | --- |
| Cloudflare Workers AI | `QUALIFIED` | Phase 6 canonical経路、ToolCall往復、audit、budget reconciliationを確認。quotaは未報告値をunknownのまま保持 |
| OpenRouter Free | `QUALIFIED` | `openrouter/free`のcanonical経路とToolCall往復を確認。quotaは未報告 |
| Ollama | `QUALIFIED` | local / privacy / survival用途 |
| Groq | `UNQUALIFIED` | `/v1/models` probeがHTTP 403。permission/account状態を推測しない |
| Mistral | `UNQUALIFIED` | キー読込み後のlive attemptはAPI HTTP 429。成功や無料枠を推測しない |
| SambaNova | `INACTIVE` | `/v1/models`は到達したが推論HTTP 429/402。free/no-charge qualification対象外 |

資格情報は環境変数または外部secret storeからのみ読み込み、repo・manifest・audit・
証跡へ値を書き込みません。

## Refactor Freezeの内容

- Controllerのprovider request実行を `runtime/model_turn.py`、compatibility direct-provider実行を `runtime/legacy_provider.py` へ分離。canonical経路は `Controller -> ProviderDispatcher` のままです
- ResourceLedgerは同一SQLite connection / lock / transaction semanticsを維持し、Catalog、Observation、Quota、Health、Budget Reservation storeを内部分離しました。schema v7は維持しています
- SQLiteStateStoreはconnection / transaction ownerを維持し、`state/schema.py`、`state/core_repository.py`、`state/effects_repository.py`へ内部整理しました
- ToolRuntimeは `tools/executor.py` と `tools/effect_guard.py`へ実行／副作用責務を分離し、timeout、process-tree kill、cancellation、approval、idempotency、reconciliation semanticsを維持しました
- ProviderRegistryは `providers/registry.py` を責務所有者とし、DispatcherはControlPlaneのSnapshot API経由でrouting/budget viewを取得します
- DevFarmテストをmanifest/outbound境界とpatch/host verificationへ、Resourceテストをmigration/control、observation/quota、budgetへ分割しました。Model自己申告tests claimは正式証拠ではなく、host側検証だけを採用します
- `src` と `tests/v2` の旧v1トップレベルimportは0件。v1実行資産は `legacy/v1-final` に隔離済みです

## DevFarm状態

worktree不存在、base revision不一致、dirty状態、symlink/out-of-root、protected path、
secret outbound、scope外patch、binary/submodule/symlink patch、patch上限超過を
fail-closedで拒否します。Cloudflare / OpenRouterの明示Worker試行はAPI到達後に
Model生成patchがstrict unified-diff検証で拒否され、host-verified Worker成功、公式branch
統合、2 Worker並列の実績はまだありません。これは外部Model出力品質の未達であり、validator
を緩めて成功扱いにはしません。

## 次の作業（Refactor後）

1. DevFarmは、承認済みmanifestで生成品質が満たせる小taskを再試行する。成功しない場合も失敗artifactを正本として保持する
2. Phase 7A/Bとして、Task profileのbounded tierを明示的なresource metadataへ接続するmodel routingを段階導入する
3. Phase 7Dのplanは、実際のretry/escalation dispatchへ自動接続せず、host側review・policy・既存ControlPlaneを経由する境界を追加する
4. G6O1、Groq、Mistral、SambaNovaの外部状態は、実証が得られるまで現在の判定を維持する

READMEは入口、`PHASE6_PLAN.md`はPhase 6の受入条件、`V2_EXECUTION_PLAN.md`はロードマップ、
`CHANGELOG.md`は履歴、`TRACEABILITY.md`は要求と実装所有者の追跡に限定します。
