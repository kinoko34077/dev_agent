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

unknownな外部効果の再送、approval／budget／privacyの迂回、model自己申告だけの
verification、自動integrationは禁止する。`resume`はartifactを再読込して依存を
解放するだけで、Providerを勝手に再実行しない。

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
