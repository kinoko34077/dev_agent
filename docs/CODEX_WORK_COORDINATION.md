# Codex Work Coordination

この文書は、既存のCommander、Supervisor、Task、Process Coordination、Host
Verificationにまたがる「作業の位置・復帰・外部送信」の運用契約を定義する。
新しいTask Scheduler、Task state machine、retry engine、Agent frameworkを追加する
文書ではない。Task UUID、parent/dependency、ownership、既存のSQLite/immutable
artifactを正本として利用する。

## 1. 番号付き開発プログラム

現在の書置きは、旧来の7項目プログラムを包含する次の8 Stageへ更新した。Work Addressは
既存Task UUID、dependency、ownership、lease、Gate IDを置換しない。Stage間は直列、同一
Stageの別laneは依存がない場合だけ並列であり、各項目は実装・focused test・必要な証拠が
揃うまで完了扱いにしない。詳細の一行正本は[`V2_DETAILED_ROADMAP.md`](V2_DETAILED_ROADMAP.md)
の「現行Work Addressプログラム」である。

1. **Stage 1 / `1-A`〜`1-E` — Phase 7 closeout / Guardian実運用化**: OS liveness、実rolling、
   実rollback、production approval persistence、D9 readinessを順に閉じる。現在はread-only
   OS診断と既存approval監査から開始し、OS登録・D9 mutationは未検証のまま維持する。
2. **Stage 2 / `2-A`〜`2-E` — D9 Real Self-Repair**: Stage 1のREADY後にだけ、Human approval付き
   bounded repair、runtime promotion、health、rollback、recoveryを1件実証する。
3. **Stage 3 / `3-A`〜`3-F` — Autonomy Safety Model**: HARD_DENY、AUTHORITY_SENSITIVE、
   NORMAL_REPO、worktree、backup、egress、Codex policyを整理し、安全回帰を通す。
4. **Stage 4 / `4-A`〜`4-E` — Phase 8 Multi-Role Foundation**: Role Manifest、Role Instance、
   ownership/admission/handoff、Planner/Implementer/Reviewerを既存境界へ接続する。
5. **Stage 5 / `5-A`〜`5-E` — Multi-Role Runtime E2E**: 2以上の非重複Implementer、独立検証、
   Reviewer、bounded refinement、Host integrationを実repositoryで成立させる。
6. **Stage 6 / `6-A`〜`6-F` — AI Company benchmark**: scenarios、metrics、独立Evaluator、
   adversarial cases、workflow promotion、survival modesを評価可能にする。
7. **Stage 7 / `7-A`〜`7-E` — Formal Operation / MCP API**: Operation監査、planning tools、
   wire、external-client E2E、compatibilityを既存Authorityへ委譲して閉じる。
8. **Stage 8 / `8-A`〜`8-G` — UI Entry Readiness**: read/control model、state、update、redaction、
   headless simulation、contract freezeを完了するまでUI実装へ進まない。

Stage 1未完の現在は、`1-A-1`/`1-A-2`（Guardian read-only診断）と`1-D-1`（既存approval
authority監査）を着手点とする。旧来の安定化/refactor/egress/Process Coordination/Guardian/D9
の説明は、このStage列の対応するlaneへ吸収されたものとして扱う。

## 2. Work Address

`task_id`は不変の機械IDとして維持し、`work_address`は表示・復帰・割込み用の
構造化アドレスとする。アドレスの保存先は既存Commander Planまたはimmutable
coordination artifactとし、UUIDをアドレスへ変換して参照を失わせない。

構文は次のとおりである。

```text
segment := positive_integer | uppercase_letter
address := segment ("-" segment)*
```

数字segmentは同一枝の順次位置、英字segmentは同一地点からの並列laneを表す。
混在は許可するため、`5-B-8-3`は有効である。数字・英字は構造上の意図だけを
示し、実際の実行可否は既存のdependency、file ownership、lease、Host policyで
決める。アドレスだけから依存関係や同時実行可否を推測しない。

TaskとStepは区別できるよう必要時に`node_type`（`task` / `step`）を持つ。ただし
既存Taskを小工程へ分解することは必須ではない。Plannerの`child_key`を一つの
segmentとして親アドレスへ付加できる。

新しい子の既定アドレスは、同じ親の既存segmentと衝突しない最小の次番号または
次の大文字laneをHost側で発番する。Commanderが外部の親TaskをPlanへ含めない場合は
root位置から発番し、`work_address_parent`を指定した場合はその親の直接の子として
発番する。`work_address_kind`はHost側の発番意図（`numeric` / `letter`）であり、
明示アドレスを受け入れる場合も、構文・親子・重複・depth上限を決定論的に検証する。
既存Commanderの`ownership`判定はそのまま排他的な正本とし、Supervisorの
`ownership`照会または`python scripts/devfarm_supervisor.py ownership <run-id>`（全Planは
`--all`）で、未完了Taskが保持するpath、owner、status、work addressを読み取り専用で
確認できる。照会はclaim/releaseを行わず、統合または明示的supersedeまで保持される
Rejected/Blockedの予約も隠さない。

## 3. 書置き・Resume Capsule

長い会話を再投入せず、意味のある開始・状態変更・安全な中断点で、既存の
immutable handoff artifactとして次のbounded projectionを保存する。

```json
{
  "task_id": "<existing Task UUID, optional for legacy capsules>",
  "work_address": "5-B-8",
  "status": "RUNNING",
  "objective": "coordination protocolを実装する",
  "current_action": "generation fencingを検証する",
  "completed": ["peer identity validation"],
  "next_action": "Mailbox境界のfocused testを追加する",
  "resume_from": "generation fencing testの直後",
  "blocked_by": [],
  "owned_paths": ["src/dev_agent/coordination/protocol.py"],
  "checkpoint_revision": "<resolved revision>",
  "artifact_refs": []
}
```

実際のpayloadは既存のartifact size、secret、path、revision検証を通す。mutableな
共有MarkdownをSSOTにせず、Mailboxはartifact referenceと配送状態だけを持つ。

`ResumeCapsule.task_id`は既存Task UUIDへの任意のリンクであり、旧書置きに無い場合も
読み取り可能なままにする。Task UUIDが得られる場合、`to_interrupt_frame()`で親の復帰点を
bounded `InterruptFrame`へ射影できる。`push_interrupt()`は現在のカプセルを
`SUSPENDED_BY_INTERRUPT`として親フレームをLIFO stackへ積み、子Task側のカプセルへ
stackを引き継ぐ。子の完了時に`pop_interrupt()`を呼ぶと、最新フレームと親の
address/next-action/resume-from/revisionの位置射影を返す。親Taskの全状態は既存の
Task/checkpointを正本とし、この値層はlookup、queue操作、Scheduler、process制御を行わない。

## 4. User intervention

ユーザー入力を受けたこと自体はキャンセルではない。既存Taskの状態とcheckpointを
先に保存し、次の種別へ分類する。

- `NOTE`: 現在の作業を止めず、後続候補として記録する。
- `PARALLEL`: ownership/dependencyが非重複なら別枝として投入する。
- `INTERRUPT`: 現在作業を安全なcheckpointでsuspendし、完了後に親へ戻る。
- `CANCEL`: 明示されたTaskだけを既存のcancel/authority経路で停止する。

種別が曖昧な入力は、現在作業を止めない`NOTE`または`PARALLEL`へ倒す。割込みは
安全なcheckpointでのみ適用し、割込み深度には既定上限8を置く。親の復帰点はLIFO
stackへ保存し、子が完了したら自動popする。parallel枝はstackへ入れず既存Task
queue/依存へ委譲する。

## 5. External egress

外部Workerへ送る判断はモデルへ毎回委ねず、Hostが次の順で決める。

```text
standing grant
  -> task ownership/path check
  -> deterministic content scan and hash
  -> per-dispatch manifest
  -> ALLOW / REVIEW / DENY
  -> ProviderDispatcher
```

読み取り、書き込み、外部送信、特別なapprovalは別capabilityである。standing grant
は低リスク開発sourceのカテゴリ許可に留め、実際に送るbytesは毎回manifestで再検査
する。manifestにはpolicy id、destination、task、path、sha256、size、sensitivity、
scan結果を含めるが、secret・credential・raw provider responseは保存しない。

`ALLOW`だけが通常のWorker送信へ進み、`REVIEW`は上位判断、`DENY`は送信せず構造化
理由を返す。`.env*`、credential、秘密鍵、protected path、path/symlink escape、
許可root外は拒否する。normalは「非公開でも外部Worker送信による影響が軽微」と
いう分類であり、公開状態そのものの意味ではない。

既存の`external_provider_allowed`、Provider approval、Path policy、Host
Verification、Privacy/Authorityを迂回しない。送信manifest内の本文に命令やprompt
injectionが含まれていてもPayloadのままであり、Control・Human Authority・security
conditionを上書きしない。

輸送失敗、policy拒否、content拒否、Provider拒否、入力不足は別カテゴリで記録する。
失敗を理由に無制限再送しない。既存UNKNOWN/reconciliationの外部効果境界も維持する。

## 6. 非対象

この契約だけではGuardian、OS Service、常駐daemon、正式なCodex external session
discovery、MCP wire transport、Compression Service、paid Provider、G6O1実用側、
D9 real self-repair、automatic push/mergeを実装済みとは扱わない。
