# Issue #127 continuation V0 — evidence and boundary map

更新日: 2026-11-01  
検証対象: `v2/bootstrap@ef530211cc0750d91fcc1f4359d2fab5c34df656`
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
| Commander / DevFarm / Supervisor | `scripts/devfarm_commander.py` → `scripts/devfarm_supervisor.py` → Host Verification → review/integration | `tests/v2/test_devfarm_commander.py`, `test_devfarm_supervisor.py`, #97/#127のbounded Worker記録 | Claim/Leaseを含む実モデルの連続運用と、同一rootの再起動復帰は別途必要 | #97 / #127 | 既存の承認済みPlanと所有権を再確認した上で、低リスク・可逆な別タスクを一件だけ実行し、task/attempt/effectをIssueへ記録 | `implemented-and-tested` |
| Worker metrics | `scripts/devfarm_worker.py` → Host検証 → `.devfarm/metrics.sqlite3` の `WorkerMetricsStore` | DBを読み取り専用で確認: 55件のhost-verified record。#134の `ollama:local:gemma4-12b / gemma4:12b` R5記録は `result_accepted=true`, cost=0。nullable tierのTypeError修正に加え、同一task/requestのpayload差し替えを拒否する回帰を追加 | metrics schemaにはtask/attempt/effect/source revisionの全相関を独立に保持する列がなく、R5の実モデル出力をDBだけで完全再現できない | #127 / DevFarm | 次の実タスクで exact task_id, attempt/effect, provider/binding/model, task_type, source_rev, Host結果をIssue/Work Artifactに束ねる | `real-world-evidenced` |
| EvidenceBasedRoutingPolicy | `src/dev_agent/intelligence/evidence_routing.py` が候補を追加せず、callerのhard-filter済み候補だけを順位付け | 実DBの `worker` / `documentation` summarize と policy rank を再実行。`worker` は実データを読み取り、受入率不足を `rollback_threshold` として返却。`documentation` はGemini 4 samplesを `evidence_eligible`、Gemma 2 samplesを `insufficient_samples` と判定。全要約の数値/時刻検証と候補外malformed fail-closed回帰も追加 | 独立した後続タスクで新証拠が自然に消費され、選択順位が変わることは未実証。期限切れ・低品質を昇格させない証拠も継続必要 | #127 / #81 | V1で新しい許可済み低リスクタスクを一件実行し、旧証拠と混同せず新しいrequest/recordで比較する。証拠不足なら `INSUFFICIENT_EVIDENCE / NO_RANK_PROMOTION` を記録 | `implemented-and-tested` |
| ResourceRouter | `src/dev_agent/resources/router.py` と既存Resource/qualification hard filter。Evidence routingは advisory のみ | Router・resource・securityの回帰テスト、および合成Host outcome→routingの#131 | 実モデルの新しい受入結果が通常routingの次タスク選択へ影響する一連の実証は未取得 | #127 / #81 | V1の候補を既存hard constraintで絞り、実績がある場合だけ advisory順位を比較。capability/billing/quota/healthを緩めない | `implemented-and-tested` |
| Operation / Queue / Wait-Wake / Survival / Recovery | `src/dev_agent/operation.py`、`src/dev_agent/scheduler/`、既存SQLite checkpoint/lease/recovery | PR #139 `test_runtime_coordinator_restart_keeps_mixed_wait_parked_without_replaying_ready_work` と `ef53021` のV4合成シーケンスが、park/restart後の一度だけの復帰と独立task継続を検証。関連144件、全v2 2088件が成功 | Provider障害・quota不足・独立作業継続・restart/rejoinを混ぜた実モデルの同一root試験は未達 | #127 / #65 / #11 | 実モデルV2は新規承認済み低リスクWorker/Hostタスクでのみ行い、外部効果を再送しない証跡をIssueへ記録。正式Gate変更はしない | `simulation-only` |
| R0–R5 acceptance | R0–R4の決定論的/合成境界と、R5のDevFarm Worker→Host→review→docs integration | #131は合成Worker出力、#134は実ローカル `gemma4:12b` のdocs task。#134 post-merge kernel 3.10/3.11/provider-smokeはgreen | V1の「次の通常routingが新証拠を消費」、V2混在耐性、V3弱モデル失敗適応、V4継続rootは未受入 | #127 | V1→V4を順序どおり一件ずつ実施し、実モデル結果と合成試験を分離してIssue/PRに記録 | `real-world-evidenced` |
| Current goals / source alignment | `docs/requirements/continuity/01-sustainable-useful-work.md`、Issue #127、Drive sources | Drive原則を読み、機械検証・回帰・証拠外部化・source precedenceをこの文書へ反映。Current Stateは`3a9f3b8` docs syncを経て、ef53021のV4オフライン証跡へ同期 | 受入済みV2/V4は合成境界のみで、実Provider/Workerの混在継続・Phase 8判断は未達 | #127 / Codex | V1–V4は実モデル成果と合成試験を分離してIssue/PRへ記録し、Drive原則に従う。資格・課金・quota・health・Gateを緩和しない | `implemented-and-tested` |
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
python -m pytest tests/v2/test_devfarm_metrics.py tests/v2/test_evidence_routing.py -q
python -m pytest tests/v2 -q
```

実DBは `.devfarm/metrics.sqlite3` を `WorkerMetricsStore.open_read_only()` で
開いた。Provider呼び出し、生成、課金、credential/permission変更、UNKNOWN
effect再送は行っていない。

## V0の結論

V0の境界地図と実証可能な証拠経路は整った。`WorkerMetricsStore` の実データ
欠陥は修正対象としてPR化する。一方、V1以降の「新しい実績が後続選択へ
自然に反映された」こと、V2–V4の実モデル継続、Formal Phase 8は未達であり、
この文書・修正PRだけでPASSや昇格を宣言しない。
