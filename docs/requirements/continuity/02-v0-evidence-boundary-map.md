# Issue #127 continuation V0 — evidence and boundary map

更新日: 2026-11-02
検証対象: `v2/bootstrap@a9bd5bc`
所有Issue: [#127](https://github.com/kinoko34077/dev_agent/issues/127)

これはIssue #127の継続ロードマップ `CONT-V0` の読み取り専用監査結果で
ある。`spec/v2/GATE_STATUS.json`、Formal Phase 8 (#65/#11)、認証・課金・
権限、release/deployの状態は変更しない。V0は境界と証拠の地図であり、
V1以降の実モデル成果やPhase 8受入を代替しない。

## 情報源と判定規則

参照したGPT共有用Driveの入口:
<https://drive.google.com/drive/folders/1OXZsepnc2FYlR8jDkgyxU53Gale_eLHN>

| 情報源 | 用途 |
| --- | --- |
| `情報源インデックス.md` (`1PbYcZ_582BeziMfX8KFvArFRCOpp3PNU`) | 情報源の正本・配置の確認 |
| `03_Development_Specification_Principles.md` (`1B-ssU0JUPHNZ2dhaMYX3yP4ulLGJLq8_`) | 機械検証、回帰、Issue/PRへの証拠外部化 |
| `04_Source_Research_and_Validation_Principles.md` | 事実・推論・仮説・評価の分離、出典と不確実性 |
| `05_Information_Organization_and_Accumulation_Principles.md` | Core/Domain/Current State/Work Artifactの分離と優先順位 |
| `依頼モード・実行権限・承認ゲート.txt` (`1Erg957wR9pvUcxJP0_FR-olP6t2qKFbF`) | 通常実装・PR・マージとHuman Gateの境界 |

判定語はロードマップで定義された次の分類を使う。

- `implemented-and-tested`: 実装経路と再現可能なテストがある。
- `real-world-evidenced`: 実Provider/Hostで観測し、受入記録がある。
- `simulation-only`: fixture・合成出力・決定論的試験のみ。
- `blocked-with-recovery`: 未達だが、所有境界・復旧条件・次の安全な行動が明示されている。
- `not-applicable`: このV0の対象外であり、別の所有Issueがある。

## V0 matrix

| 領域 | 実際の経路 | 現在の証拠 | 欠落するseam | owner | first executable next action | 判定 |
| --- | --- | --- | --- | --- | --- | --- |
| Commander / DevFarm / Supervisor | `scripts/devfarm_commander.py` → `scripts/devfarm_supervisor.py` → Host Verification → review/integration | `tests/v2/test_devfarm_commander.py`, `test_devfarm_supervisor.py`, #97/#127のbounded Worker記録。2026-11-02のfresh local attemptsはqwen/gemmaともHost受入前に終了し、各Planを公式CLIでsupersedeして証跡を保持。`57e6033` はCodex直接のnew-file Host回帰でありWorker成功ではない | Claim/Leaseを含む実モデルの連続運用と、同一rootの再起動復帰は別途必要 | #97 / #127 | 新しい独立Task・無課金binding・未使用のrequest identityが同時に確認できる場合だけ、承認済みPlanを一件実行し、task/attempt/effectをIssueへ記録。既存UNKNOWNは再送しない | `implemented-and-tested` |
| Worker metrics | `scripts/devfarm_worker.py` → Host検証 → `.devfarm/metrics.sqlite3` の `WorkerMetricsStore` | DBを読み取り専用で確認: 55件のhost-verified record。#134の `ollama:local:gemma4-12b / gemma4:12b` R5記録は `result_accepted=true`, cost=0。nullable tierのTypeError修正、同一task/requestのpayload差し替え拒否、`c47b43b` の schema v2 `attempt_id`・`verification_id`・`source_revision` 相関とv1→v2 migration、`acabfc9` のv1 read-only list/summarize回帰、`407d5d4` の通常Host Verificationが書込み済み `verification_id` をmetricsへ束ねる回帰、`529f1f2` のv1→v2 migration crash rollback回帰、`06913dd` のexact replay timestamp不変回帰、`f45a1f9` の別接続間identity検証/書込み原子化回帰、`03005ee` の開始同期・取引外SELECT検出・実際の勝者追跡回帰、`53eb99d` の同一payload並行再送・鮮度不変回帰、`011e925` の明示 `new_files` 出力契約とHost側新規patch materialization、`57e6033` の独立Host検証回帰を確認。最新の並行再送focused回帰は20回反復で全GREEN。 | 既存55件のlegacy行は相関列がnullのままで、実R5の次回観測で実値を埋める必要がある。今回のfresh Worker失敗は受入済みmetricsへ昇格していない。DB単体ではレビュー/統合のIssue/Work Artifact lineageや、Provider request以外の外部effectを推測しない | #127 / DevFarm | 次の実タスクで exact task_id, attempt/effect(request_id), provider/binding/model, task_type, source_rev, Host結果、review/integration refをIssue/Work Artifactに束ねる | `real-world-evidenced` |
| EvidenceBasedRoutingPolicy | `src/dev_agent/intelligence/evidence_routing.py` が候補を追加せず、callerのhard-filter済み候補だけを順位付け | 実DBの `worker` / `documentation` summarize と policy rank を再実行。`worker` は実データを読み取り、受入率不足を `rollback_threshold` として返却。`documentation` はGemini 4 samplesを `evidence_eligible`、Gemma 2 samplesを `insufficient_samples` と判定。全要約の数値/時刻検証と候補外malformed fail-closed回帰も追加 | 独立した後続タスクで新証拠が自然に消費され、選択順位が変わることは未実証。期限切れ・低品質を昇格させない証拠も継続必要 | #127 / #81 | V1で新しい許可済み低リスクタスクを一件実行し、旧証拠と混同せず新しいrequest/recordで比較する。証拠不足なら `INSUFFICIENT_EVIDENCE / NO_RANK_PROMOTION` を記録 | `implemented-and-tested` |
| ResourceRouter | `src/dev_agent/resources/router.py` と既存Resource/qualification hard filter。Evidence routingは advisory のみ | Router・resource・securityの回帰テスト、および合成Host outcome→routingの#131 | 実モデルの新しい受入結果が通常routingの次タスク選択へ影響する一連の実証は未取得 | #127 / #81 | V1の候補を既存hard constraintで絞り、実績がある場合だけ advisory順位を比較。capability/billing/quota/healthを緩めない | `implemented-and-tested` |
| Operation / Queue / Wait-Wake / Survival / Recovery | `src/dev_agent/operation.py`、`src/dev_agent/scheduler/`、既存SQLite checkpoint/lease/recovery | PR #139 `test_runtime_coordinator_restart_keeps_mixed_wait_parked_without_replaying_ready_work` と `ef53021` のV4合成シーケンスが、park/restart後の一度だけの復帰と独立task継続を検証。関連144件、全v2 2088件が成功 | Provider障害・quota不足・独立作業継続・restart/rejoinを混ぜた実モデルの同一root試験は未達 | #127 / #65 / #11 | 実モデルV2は新規承認済み低リスクWorker/Hostタスクでのみ行い、外部効果を再送しない証跡をIssueへ記録。正式Gate変更はしない | `simulation-only` |
| R0–R5 acceptance | R0–R4の決定論的/合成境界と、R5のDevFarm Worker→Host→review→docs integration | #131は合成Worker出力、#134は実ローカル `gemma4:12b` のdocs task。#134 post-merge kernel 3.10/3.11/provider-smokeはgreen。今回のnew-file経路は`57e6033`のCodex直接Host回帰で閉じたが、fresh Worker受入は未達 | V1の「次の通常routingが新証拠を消費」、V2混在耐性、V3弱モデル失敗適応、V4継続rootは未受入 | #127 | V1→V4を順序どおり一件ずつ実施し、実モデル結果と合成試験を分離してIssue/PRに記録 | `real-world-evidenced` |
| Current goals / source alignment | `docs/requirements/continuity/01-sustainable-useful-work.md`、Issue #127、Drive sources | Drive原則を読み、機械検証・回帰・証拠外部化・source precedenceをこの文書へ反映。Current Stateは`a9bd5bc` docs syncを経て、#142 read-boundary investigationと`57e6033` Host回帰へ同期 | 受入済みV2/V4は合成境界のみで、実Provider/Workerの混在継続・Phase 8判断は未達 | #127 / Codex | V1–V4は実モデル成果と合成試験を分離してIssue/PRへ記録し、Drive原則に従う。資格・課金・quota・health・Gateを緩和しない | `implemented-and-tested` |
| Formal Phase 8 (#65/#11) | `spec/v2/GATE_STATUS.json` と所有Issue | 現在も `PHASE8_LIVE_ACTIVATION=NOT_VERIFIED`、#65/#11が正式受入 authority | R5/V0の実績はFormal Phase 8を満たさず、実モデルの資格・課金・quota・health・role・continuationの独立証拠が不足 | #65 / #11 | 安全なV1–V4証拠を積むだけに留め、資格・課金・権限・Gateを変更しない。正式判定は所有Issueでのみ行う | `blocked-with-recovery` |

## 再現可能なV0検証

修正前の最小回帰は、同一の `provider/binding/model` で
`intelligence_tier=None` と `"L1"` を記録して
`WorkerMetricsStore.summarize(task_type="worker")` を呼ぶと、Pythonの異なる
型の比較で `TypeError` になることを確認した。修正後はsort keyだけを
`tier or ""` に正規化し、返却値のtierは元の `None` / `"L1"` を保持する。

実行した検証:

```text
python -m pytest tests/v2/test_devfarm_metrics.py::test_worker_metrics_summarize_handles_nullable_tiers -q
python -m pytest tests/v2/test_devfarm_metrics.py::test_worker_metrics_migrates_v1_schema_without_rewriting_legacy_observation -q
python -m pytest tests/v2/test_devfarm_metrics.py::test_worker_metrics_open_read_only_accepts_legacy_v1_schema -q
python -m pytest tests/v2/test_devfarm_metrics.py::test_host_verification_metrics_bind_to_written_verification_record -q
python -m pytest tests/v2/test_devfarm_metrics.py::test_worker_metrics_migration_failure_rolls_back_to_readable_v1 -q
python -m pytest tests/v2/test_devfarm_metrics.py::test_worker_metrics_upsert_is_idempotent_and_does_not_use_model_claims -q
python -m pytest tests/v2/test_devfarm_metrics.py::test_worker_metrics_rejects_concurrent_conflicting_replay_across_connections -q
python -m pytest tests/v2/test_devfarm_metrics.py::test_worker_metrics_accepts_concurrent_exact_replay_without_refreshing_timestamp -q
python -m pytest tests/v2/test_devfarm_metrics.py tests/v2/test_evidence_routing.py -q
python -m pytest tests/v2 -q
```

同じfocused concurrency testを20回連続実行して全て成功した。さらに
`BEGIN IMMEDIATE`を一時的に除去するmutationでは、2つの接続が取引外で
identityを選択したことを検出してREDになり、修正を復元した後に再びGREENへ
戻った。テストはどちらのpayloadが実際に先にcommitされたかを観測し、特定の
workerの勝利を仮定しない。

実DBは `.devfarm/metrics.sqlite3` を `WorkerMetricsStore.open_read_only()` で
開いた。Provider呼び出し、生成、課金、credential/permission変更、UNKNOWN
effect再送は行っていない。

## V0の結論

V0の境界地図と実証可能な証拠経路は整った。現行HEADでは `WorkerMetricsStore` の
correlation schemaは `c47b43b` で導入され、`407d5d4` で通常のHost
Verification実行後も実際のverification artifactへ結合されるよう修正された。
`06913dd` では完全再送による `recorded_at` のfreshness延長も防止し、
`f45a1f9` では別SQLite接続間でもidentity検証と書込みを同一transactionに束ねて
競合するpayloadの上書きを防止し、`03005ee` ではその回帰が実行順序や特定の
勝者に依存しないことを20回反復とmutation REDで確認し、`53eb99d` では同一payload
の並行再送が双方成功しつつ元の鮮度を延長しないことを20回反復で確認したが、
既存legacy行への遡及補完は行っていない。一方、V1以降の「新しい実績が後続選択へ
自然に反映された」こと、V2–V4の実モデル継続、Formal Phase 8は未達であり、
この文書・修正PRだけでPASSや昇格を宣言しない。`a9bd5bc`時点の全
`tests/v2` は `2105 passed, 1 skipped`、exact-head Core/Provider Smokeも成功しているが、
それはV1の新規Worker成果、V2の実Provider混在、V4の継続rootを代替しない。
なお、Discord scopeの選択後読取については#142の現行コード追跡でConsumer不在を確認した。
これは将来のread/open境界要件を記録したもので、Discord scopeをOS sandboxと見なす根拠ではない。
