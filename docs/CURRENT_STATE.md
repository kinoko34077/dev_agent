# Current State — v2/bootstrap

最終同期時点の実装基準は `8b638a9`（Evaluator証跡のdurable記録を追加した
code commit）です。この文書は現在の実装・検証・外部状態をまとめる正本であり、
Gate判定は [`spec/v2/GATE_STATUS.json`](../spec/v2/GATE_STATUS.json) を正とします。

## 判定

- Phase 6 foundation: `VERIFIED`
- Phase 6 operational: `G6O2`〜`G6O6` は `VERIFIED`
- `G6O1`: `BLOCKED_EXTERNAL`（実paid Providerのworst-case課金実証と、deployment-owned budget設定の外部保護が必要）
- Gate statusはこの文書同期では変更していません。G6O1を理由にfake evidenceを作らず、Phase 6の未達をコード不足と混同しません。

## 検証

- v2ローカル全回帰: `349 passed, 1 skipped`
- skip: Windows ACLはdeployment-owned
- `8b638a9`に対する最新exact-head GitHub Actions結果: この同期時点では未確認
- 過去のCI結果は過去のcode baselineの証跡であり、現HEADの成功とは扱いません

## Provider状態

| Provider | 状態 | 備考 |
| --- | --- | --- |
| Cloudflare Workers AI | `QUALIFIED` | Phase 6 canonical経路、ToolCall往復、audit、budget reconciliationを確認。quotaは未報告値をunknownのまま保持 |
| OpenRouter Free | `QUALIFIED` | `openrouter/free`のcanonical経路とToolCall往復を確認。quotaは未報告 |
| Ollama | `QUALIFIED` | local / privacy / survival用途 |
| Groq | `UNQUALIFIED` | `/v1/models` probeがHTTP 403。permission/account状態を推測しない |
| Mistral | `UNQUALIFIED` | キー読込み後のlive attemptはAPI HTTP 429。証跡は`spec/v2/evidence/phase6-mistral-2026-09-09.json` |
| SambaNova | `INACTIVE` | `/v1/models`は到達したが推論HTTP 429/402。free/no-charge qualification対象外 |

資格情報は環境変数または外部secret storeからのみ読み込み、repo・manifest・audit・
証跡へ値を書き込みません。

## 実装済みのrefactor / Worker境界

- 正規Provider経路は `Controller -> ProviderDispatcher -> ProviderRegistry -> concrete Provider`。
- OpenAI互換HTTPは共通Transport / Providerへ集約し、Factory経由で構築します。
- Registryは `provider_id` と `provider_binding_id` を分離し、同一vendorの複数model/bindingを表現できます。
- DevFarmはworktree不存在、base revision不一致、dirty状態、symlink/out-of-root、protected path、secret outbound、scope外patchをfail-closedで拒否します。
- 外部送信はCodex/operatorが明示起動し、manifestの`outbound_files`と承認Providerだけを対象にします。自動activation・無承認送信・自動patch適用・公式branchへの自動変更はありません。
- Workerの`changed_files`とtests自己申告は正式証拠ではありません。unified diffの実pathを検証し、host側検証結果を別artifactへ記録します。
- Cloudflare / OpenRouterのWorker試行はAPI到達後にModel生成patchがstrict `git apply --check`で拒否されました。host-verified test、公式branch統合、Worker成功実績はまだありません。
- ResourceLedgerは同一SQLite connection / transaction semanticsを維持したままCatalog / Observation / Quota / Healthの内部storeを分離しています。
- Phase 7Cの決定的Evaluatorとdurable `evaluation.recorded` eventを追加済みです。EvaluatorはModel自己評価を使わず、host側の決定的証拠から有限な判定を行います。

## 次の作業

1. `8b638a9`以降のexact-head CIを外部確認し、この文書のCI状態を更新する。
2. DevFarm Workerの成功条件を満たす小さなpatch proposalを、同じfail-closed境界で再試行する（無理に成功扱いしない）。
3. ResourceLedgerの残存Budget store、Controllerのlegacy executor、StateStore / ToolRuntimeの内部整理を小さなsliceで継続する。
4. Phase 7はEvaluatorから、有限なescalation policyと実行統合へ進める。ただしmodel-tier routing、AgentBackend、MCP、自己改善の自動化は未実装です。
5. G6O1、Groq、Mistral、SambaNovaの外部状態は、実証が得られるまで現在の未資格化・blocked判定を維持する。

READMEは入口、`PHASE6_PLAN.md`はPhase 6の受入条件、`V2_EXECUTION_PLAN.md`はロードマップ、
`CHANGELOG.md`は履歴、`TRACEABILITY.md`は要求と実装所有者の追跡に限定します。
