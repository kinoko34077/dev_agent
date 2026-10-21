# Codex Commander運用

この文書は、Codexが開発作業を自身とDevFarm Workerへ安全に分解するための
development-only手順です。Production RuntimeのMulti-Agent、AgentBackend、
MCP、Phase 8 Role runtimeではありません。

## 運用サイクル

```text
目標受領
  ↓
CURRENT_STATE / SYSTEM_MAP / 要件確認
  ↓
Task分解・file ownership・依存DAG決定
  ↓
Codex担当とWorker担当を分離
  ↓
親Planをdurably作成
  ↓
READYな独立WorkerだけRemote proposalへdispatch
  ↓
result / patchをcollect
  ↓
Host Verification（小さいqueue）
  ↓
Codex review / 必要ならbounded reassign
  ↓
明示的integration記録・依存Task release
  ↓
full regression・docs/evidence同期
```

Worker同士は直接通信しない。依存成果はresult artifactをCodexが読み、次の
manifestへ必要な情報だけを移す。Codex自身の設計・security-sensitive変更・
conflict解消・公式branchへの適用は、同じPlan内の `owner=codex` Taskとして
追跡できる。

## M2: 上位目的の反証型レビュー（devflow #761 / dev_agent #103）

非自明な新機能・UI再設計・処理経路変更では、**親Plan作成前**に元要求と採用する設計案の意味的整合を評価する。これは既存のCommanderに付随するレビューであり、新しいStateStore・実行権限・Phase8 Gateではない。

1. **正本確認:** owning Issueと現行の個別仕様を実際に取得する。旧IssueやM1の文章だけで最新仕様を代用しない。原要求と出典、上位目的、固定条件、利用者が実現できる状態を確認し、推定を確定事項にしない。
2. **設計案を具体化:** 単に「モデル切替を実装する」で終わらせず、設定場所・データ処理経路・利用時に観測する効果・禁止された代替案・同等機能のownerまで書く。複数案に必要性がなければ形式的な案の数を増やさない。
3. **反証:** 設計を支持する説明とは別に、「要求を満たさずにコード/CIだけが成功するシナリオ」「既存の似た機能との重複」「プラットフォーム標準操作の誤解」「ユーザーが求めていない新規機能への置換」を探す。**違反が見つかれば提案を修正**し、現行正本との根拠を残す。自分が生成した案についての自己評価を、独立した第三者レビューと称さない。
4. **限定的な機械検証:** 見つけた意味上の `satisfies / contradicts / unresolved` を、出典と提案の実在引用、理由に対応付ける。開発用 `python scripts/devfarm.py goal-check <packet.json>` が宣言・根拠・網羅性を検証するが、**Python処理だけで意味的真偽を認定しない**。外部資料のSHA-256は取得時点のtextへ紐付き、GitHub live HEADを保証しない。
5. **計画の同一性:** 実際の計画JSONを準備し、`python scripts/devfarm.py goal-plan-text <plan.json>` で監査対象の正規表現を得る。その**完全一致文字列**をpacketの `proposal` としてモデルが読む。必要な参照と短い引用箇所をpacketに記録し、`python scripts/devfarm.py plan <plan.json> --goal-check <packet.json>` を実行する。実際のPlanが変わると検証は不一致で拒否される。タスク名だけの表層的な同一性を証拠にしない。
6. **反例で評価:** [Frequency Warp #273](https://github.com/kinoko34077/frequency-warp-vst/issues/273)では独立1.5秒試聴と既存リアルタイム処理を、[devflow #767](https://github.com/kinoko34077/devflow/issues/767)では桁別ドロップダウンとiPhone式ロール入力を、正解ラベルを伏せた状態で判定する。失敗した場合は「証拠JSONを修正」だけではなく、要求解釈・設計・監査方法そのものを見直す。
7. **負荷の比例:** 親目的を継承する軽微変更・既存小修正では原則として既存経路を維持する。事前監査の省略を実装完了・M3実機受入と混同しない。未確定の仕様、権限/課金/不可逆操作は既存Human Gateへ個別に送る。

**未実施を見える化:** `devfarm.py plan` は、`--goal-check` がなく、タスクのリスクが `high/critical` または種別が `architecture/design/ui/ux` の場合に、標準エラーへ `M2_GOAL_REVIEW_NOT_RECORDED` 警告を出す。これは監査の記録漏れを示す**非ブロッキング・推定的な通知**であり、計画作成・権限・Gateは変えない。実際の要求やリスクがタスク種別に正しく記録されていなければ検知できない。したがって、警告がないことを「目的をレビュー済み」や「仕様に適合」と解釈しない。恒常的な適用・自律的な意味判断の受入は #103 にて別途検証する。

`ALIGNED`は「指定レビュー担当者の報告と提出された資料の形が整合」という**助言的結果**であり、実装の正しさや利用者の成果の証明ではない。要件に矛盾する場合は設計し直し、根拠不足では関連Sourceを調べ直す。小さな子Issueは親の目的を参照し、独立したGoal DBを作らない。

## 親Planの最小契約

Planは `.devfarm/plans/<run_id>.json` にatomic writeされる。machine-readableな
形は [`spec/v2/DEVFARM_COMMANDER_PLAN_SCHEMA.json`](../spec/v2/DEVFARM_COMMANDER_PLAN_SCHEMA.json)、
Python validationは `scripts.devfarm_commander.validate_plan()` が所有する。

必須の概念は次のとおり。

- `run_id`、`objective`、`base_revision`
- `tasks[]`: `task_id`、`owner`（`codex` / `worker`）、status、ownership、依存、試行上限
- `dependencies[]`: task間のDAG
- `ownership[]`: taskごとの非重複path集合
- `assignments[]`: WorkerのProvider/modelまたはCodex担当
- `results[]`: proposal、host verification、integrationのartifact参照

Workerへ委譲できるTaskには `worker_candidate=true` と適格性の理由を記録する。
Codexが担当する場合も、`protected`、`cross_cutting`、`architecture`、
`integration`、`no_qualified_worker`、`delegation_overhead` などの理由を残し、
委譲しなかった判断を後から再構築できるようにする。これは利用率KPIではなく、
安全なTask分解の監査情報である。

Supervisorのcompact出力には、このTask情報から導出した`delegation` summaryが含まれる。
`worker_candidate=true`のCodex Taskだけを直接実装候補として数えるため、Codexが
reviewやintegrationを担当しただけでは実装実績にならない。Planの`owner`、
`worker_candidate`、`delegation_reason`がこの集計の正本であり、未記録の変更量やtoken量は
推測しない。

Worker Taskは既存manifestを参照し、manifestの`allowed_files`がPlanのownershipを
越えないことを検査する。Codex Taskに外部Provider manifestは不要である。

## CLI

```text
python scripts/devfarm.py plan .devfarm/plan-input.json --root .
python scripts/devfarm.py status <run-id> --root .
python scripts/devfarm.py dispatch <run-id> --root .
python scripts/devfarm.py collect <run-id> --root .
python scripts/devfarm.py verify <run-id> --root .
python scripts/devfarm.py resume <run-id> --root .
python scripts/devfarm.py reassign <run-id> <task-id> --provider openrouter --model openrouter/free --root .
python scripts/devfarm.py mark-integrated <run-id> <task-id> --note "Codex reviewed" \
  --target-ref v2/bootstrap --integration-revision <commit> \
  --source-attempt-id <attempt-id> --verified-patch-digest <sha256> --root .
```

`dispatch`はPlanのassignmentにあるProvider/modelを使う。全Workerを一時的に同じ
qualified Providerへ送る場合だけ`--provider`と`--model`で明示上書きできる。
ProviderFactory／DevFarm activation policyを経由し、未activate Providerは拒否する。

`dispatch`はRemote proposalだけを行い、worktreeを作成しない。`verify`が既存の
Host Verificationを呼び、valid proposalだけを専用worktreeへ適用する。成功しても
公式`v2/bootstrap`へ自動merge、commit、Gate promotionは行わない。Codexがreview後に
`mark-integrated`を明示的に記録する。

日常運用では、SupervisorのCLI adapterを使ってreview・rework・integrationを行う。

```text
python scripts/devfarm_supervisor.py run <run-id> --root . \
  --trust-level TRUSTED_HOST_EXEC --operator-approved
python scripts/devfarm_supervisor.py review <run-id> <task-id> --root . \
  --attempt-id <attempt-id> --decision APPROVE_INTEGRATION \
  --evidence-ref <verification-artifact>
python scripts/devfarm_supervisor.py integrate <run-id> <task-id> --root . \
  --decision-id <decision-id> --target-checkout . \
  --target-ref HEAD --commit-message "<message>"
python scripts/devfarm_supervisor.py codexless <run-id> <task-id> --root . \
  --proposal-file <review-proposal.json> \
  --shadow-evidence-file <shadow-a.json> \
  --shadow-evidence-file <shadow-b.json>
```

REWORK時は`review --decision REWORK --required-correction ...`を先にdurably記録し、
その後`rework`で差分Handoff付きimmutable manifestを再発行する。CLIは既存Commander
APIを呼ぶだけで、Host Verification、approval、Git証拠、dependency releaseの責務を
複製しない。Worker作業中は`run`を使い、単なるdispatchだけでHumanへ戻らない。
日常のpreflight、unfinished Planのresume、checkpoint、restartは
[`docs/CODEX_DAILY_DOGFOOD.md`](CODEX_DAILY_DOGFOOD.md)を参照する。

CodexがWorker完了を待つ場合は、`scripts/devfarm_supervisor.py`の
`CodexSupervisedCommanderRun`を使う。`advance()`は一回のbounded snapshot pass、
`run_until_intervention()`は同じPlanをWorker terminal／Host Verification／review要求／
安全停止点まで内部継続するblocking入口である。実行中は既存Planのoptional
`supervisor` projectionへ`WAITING_FOR_WORKER`、次動作、1/5/10/15分の再開目安、
compact wakeを保存する。raw conversationをCodexへ繰り返し流したり、HOST_VERIFIEDだけで
自動integrationしたりしない。詳細は[`docs/CODEX_SUPERVISOR.md`](CODEX_SUPERVISOR.md)を参照する。

## 状態と失敗

Taskは`PLANNED → READY → DISPATCHED → PROPOSED → HOST_VERIFIED → INTEGRATED`を
基本とする。proposal失敗は`REJECTED`、Provider外部待ちは`BLOCKED`とし、Planは
失敗を成功へ読み替えない。通常のcode dependencyは依存Taskが`INTEGRATED`になるまで
後続Taskを`READY`へ進めない。`HOST_VERIFIED`は候補成果の検証済みを示すだけで、コード
依存を解放しない。調査資料やbenchmarkなどのartifact dependencyを導入する場合も、
別のtyped dependencyとして明示し、通常のcode dependencyを緩めない。
依存cycle、ownership overlap、attempt上限、scope外
manifestは作成／再割当時にfail-closedで拒否する。

`INTEGRATED`はメモだけでは成立しない。`target_ref`、`integration_revision`、
`source_attempt_id`、`verified_patch_digest` を保存し、Worker成果はHost Verification
済みpatchが実際にintegration revisionへ反映されていることをGitで検証する。
依存Taskのmanifestは、統合後のrevisionを基準にした新しいmanifestとして再発行し、
旧manifestを履歴に残す。

Plan内のownership overlapに加え、`CommanderPlanStore.create()`は未完了Planを
横断してactive Taskのownershipを照合する。`INTEGRATED`/`SUPERSEDED`済みTaskの
所有権は解放されるが、`READY`、`DISPATCHED`、`PROPOSED`、`HOST_VERIFIED`、
`REJECTED`、`BLOCKED`等の未完了Taskは、別Planから同一pathまたは親子pathを二重取得
できない。作成時は短い全体lockを持つため、同時作成のcheck-then-write競合も
boundedに防ぐ。この機構はTask ownershipだけを扱い、process lifecycleやSchedulerを
追加しない。

unknownな外部効果の再送、approval／budget／privacyの迂回、model自己申告だけの
verification、自動integrationは禁止する。`resume`はartifactを再読込して依存を
解放するだけで、Providerを勝手に再実行しない。Supervisorのreview decisionは
attempt/evidenceと結合してPlanへ保存し、REWORKは差分Handoffを付けた新manifestへ、
APPROVE_INTEGRATIONは明示承認後のHost側Git integration helperへ接続する。

作業位置の表示・割込み復帰・外部送信manifestは、[`CODEX_WORK_COORDINATION.md`](CODEX_WORK_COORDINATION.md)
を参照する。`task_id`は不変UUIDのまま、`work_address`（例 `5-B-8-3`）を任意の
表示/復帰projectionとして付与する。数字/英字の形だけで依存や並列実行を決めず、
既存のdependency・ownership・leaseを正本にする。NOTE/PARALLEL/INTERRUPT/CANCELの
分類、bounded Resume Capsule、LIFO復帰stackは新しいSchedulerではなく既存Planと
immutable Coordination artifactへ記録する。

外部Workerへ渡すbytesは、standing egress grantだけでは確定しない。Hostが既存の
path/protected/secret/privacy/provider policyを通し、dispatchごとのhash・size・
sensitivity付きmanifestを発行し、`ALLOW`だけを送信する。`REVIEW`/`DENY`、transport
failure、Provider拒否、UNKNOWN external effectは別結果として扱い、下層モデルや
manifest本文へAuthorityを移さない。

`codexless`は既存のcleanなReviewer Shadow evidenceとHost Verificationを使って、
低risk・非protected・既知Task classのroutine candidateをread-only評価するだけである。
`CANDIDATE`はReviewerのdecision authority、公式branchへのmerge/push、Gate昇格、
無条件integrationを意味しない。F0〜F2 Self-ImprovementのObservation、Diagnosis、
Improvement Planもproposal-onlyであり、Commander PlanやRepairへ自動接続しない。

## Codexの統合チェックリスト

1. branch / HEAD / dirty stateを確認したか
2. Taskごとのfile ownershipは非重複か
3. manifestのoutbound scopeとProvider approvalを確認したか
4. proposalのpatch pathをhostで検証したか
5. Host testのexit codeを確認したか（model claimは証拠にしない）
6. Worker metricsとresult referenceを回収したか
7. dependencyとbounded attemptを更新したか
8. protected領域・Gate・Current Stateを無断変更していないか
9. full regressionと文書同期後にcommit/pushしたか
