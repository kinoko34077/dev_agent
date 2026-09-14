# Codex Work Coordination

この文書は、既存のCommander、Supervisor、Task、Process Coordination、Host
Verificationにまたがる「作業の位置・復帰・外部送信」の運用契約を定義する。
新しいTask Scheduler、Task state machine、retry engine、Agent frameworkを追加する
文書ではない。Task UUID、parent/dependency、ownership、既存のSQLite/immutable
artifactを正本として利用する。

## 1. 番号付き開発プログラム

この順序を当面の書置きとして扱う。各項目は、実装・focused test・必要な証拠が
揃うまで次の依存項目を完了扱いにしない。

1. **安定化** — 現行HEAD、Gate、local regression、文書drift、UNKNOWN/reconciliation、
   Worker failureを確認し、確認できた不整合だけを最小修正する。
2. **限定refactor** — private cross-module依存、重複するResource/Artifact/Verification
   知識、active Plan間のownership重複を既存public boundaryへ整理する。
3. **作業アドレスと復帰点** — UUIDとは別のhuman-readable address、checkpoint、
   interrupt stack、NOTE/PARALLEL/INTERRUPT/CANCELを既存Plan/Coordination artifactへ
   接続する。
4. **送信許可** — standing egress grantとdispatchごとのcontent-scanned manifestを
   Host側で発行し、read/write/external-send/approvalを分離する。
5. **Process Coordination** — peer identity、generation、presence、durable mailbox、
   immutable handoffを検証する。これはTask Planeを置換しない。
6. **Guardian準備** — ControlRequest、generation fencing、drain、checkpoint、
   restart、revision-pinned runtime、rollbackを、前項の証拠を使って段階実装する。
7. **D9 real controlled repair** — 1〜6が実証された後だけ、Human approval付きの
   bounded・non-protected・rollback可能なruntime更新を一件検証する。

現在は3と4、および5のdurable foundationが実装済みである。ControlRequestと
generation-fenced Guardian policyは6のread-only準備までで、process操作は持たない。
full Guardian、drain、restart、revision-pinned runtime、rollbackは未実装・未検証であり、
7のD9 real controlled repairはproposal-only境界を越えない。

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
