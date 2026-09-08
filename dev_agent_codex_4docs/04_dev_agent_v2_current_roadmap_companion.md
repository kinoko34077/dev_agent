# dev_agent v2 現行ロードマップ 補助資料

Status: COMPANION / SUPPORTING REQUIREMENTS  
Parent: `dev_agent v2 現行ロードマップ 簡易版`

---

# 0. 本資料の役割

本資料は現行ロードマップに対し、

- 各Phaseで落としてはいけない条件
- 後段のために先に固定しておく境界
- Phase完了を誤判定しやすい点
- 対応するFoundation Requirements

を補足する。

ロードマップ本体の、

```text
何を
どの順番で
作るか
```

は再定義しない。

---

# 1. 共通Gate原則

全Phase共通で以下を適用する。

## G-01 PASS条件

`PASS`は、

```text
Specified
+
Implemented
+
Integrated
+
Verified
```

を満たした場合のみ。

関数・class・設定項目の存在だけではPASSにしない。

## G-02 Fault Verification

安全性・耐障害性に関わる条件は、正常系testだけでは完了扱いしない。

最低限、

- timeout
- malformed
- process crash
- permission denial
- duplicate execution
- inconsistent state
- external ambiguity

のうち関連するfaultを試験する。

## G-03 Exact HEAD Evidence

過去commitで通ったtestを現HEADの証拠にしない。

Evidenceには可能な限り、

```text
commit SHA
test ID
environment
observed_at
```

を結び付ける。

## G-04 BLOCKED ≠ PASS

credential、quota、外部service等の理由で検証できない場合、

```text
BLOCKED
```

とする。

未検証を実装済みという理由だけでPASSにしない。

## G-05 下位Gate再失効

上流Contractを変更した場合、それに依存する下流Gateは再検証対象とする。

---

# 2. Phase 0 補助条件

対応FND:

```text
FND-32 Repository Baseline
FND-33 Dependency Boundary
FND-34 Specification Traceability
FND-35 Machine-readable Gate Governance
FND-36 Gate Evidence Integrity
FND-37 CI Reproducibility
FND-40 Current State Documentation
```

## 外してはいけない条件

- v1 baselineをremote上でも保持。
- v1とv2のRuntime依存を分離。
- v1は実行依存ではなくfixture/evidenceとして扱う。
- spec / implementation / testの対応を追跡可能にする。
- Current StateとTarget Specificationを混同しない。
- legacy dependency障害でv2 Kernel CIが落ちない。

## 見落としやすい問題

### Baselineはbranch名だけでは弱い

可能なら、

```text
branch
+
immutable tag
+
commit SHA
```

で基準を固定する。

### CI自体が古い仕様を検証する危険

testが存在しても、現accepted specを証明しているとは限らない。

---

# 3. Phase 1 補助条件

対応FND:

```text
FND-01 Protocol / Domain Model
FND-04 Durable State SSOT
FND-05 Schema Version / Migration
FND-29 Recovery Plane Independence
FND-31 Recovery Diagnostics
FND-39 Error Semantics
```

## 外してはいけない条件

- Provider固有objectをCoreへ漏らさない。
- internal ID / provider IDを分離。
- protocol round-trip。
- invalid / unknown inputの規則。
- Recoveryは通常Runtimeなしでも起動可能。
- Recovery診断は勝手にStateを修復しない。

## 見落としやすい問題

### Serialization成功 ≠ 長期互換

将来、

- enum追加
- field rename
- optional→required
- schema version更新

が起こる。

ProtocolとState migrationを別問題として管理する。

---

# 4. Phase 2 補助条件

対応FND:

```text
FND-02 Deterministic Runtime Control
FND-03 Cancellation Semantics
FND-39 Error Semantics
```

## 外してはいけない条件

有限実行をControllerが所有する。

LLMへ以下を決めさせない。

```text
終了条件
retry ceiling
Tool call ceiling
Task expansion
deadline
```

## 特に注意

初期alphaで、

```text
max_steps
max_model_calls
max_tool_calls
```

だけ満たしていても、

production-grade bounded execution完成とはしない。

後で必要になる、

- hard timeout
- cancellation
- process containment
- size limit

はPhase 3.5で閉じる。

---

# 5. Phase 3 補助条件

対応FND:

```text
FND-04 Durable State
FND-05 Migration
FND-07 Crash Consistency
FND-08 Idempotency
FND-09 Tool Contract
FND-10 Prepared Tool Call
FND-11 Filesystem Boundary
FND-12 Human Approval
FND-13 External Effect
FND-14 Reconciliation
FND-24 Task Graph Durability
```

## 外してはいけない条件

Stateの正本はprocess memoryではなくdurable store。

Toolは単なるCallableではなく、

```text
schema
permission
timeout
side effect
idempotency
```

を持つ契約として扱う。

## 特に注意

### Local side effectもCrashの影響を受ける

外部APIだけでなく、

```text
file write
process execution
local database mutation
```

も、

> side effect成功 → result保存前crash

を考える。

### Idempotency identityはModel所有にしない

Kernel側でoperation identityを確定する。

---

# 6. Phase 3.5 補助条件

対応FND:

```text
FND-02〜14
FND-25 Event/Audit
FND-26 Sensitive Data
FND-35〜40 Governance/Fault/Error
```

ここは現在最重要。

## Phase 3.5の本質

単なるintegration testではなく、

> **Kernel Trust Boundary Closure**

として扱う。

## 6.1 Tool実行

必須:

- full schema validation
- output validation
- unsupported schema拒否
- canonical effective arguments
- byte limits
- timeout semantics
- hard containment方針

### 注意

Thread timeoutは実行停止を保証しない。

## 6.2 Approval

必須:

```text
exact Task
exact ToolCall
exact effective arguments
one-shot
expiry
revocation
atomic consumption
```

人間が見た内容とhandler実行内容を一致させる。

## 6.3 External Effect

dispatch前後を区別。

最低概念:

```text
PREPARED
DISPATCHING
SUCCEEDED
CONFIRMED_FAILED
UNKNOWN
RECONCILING
```

### 絶対条件

dispatch後の、

- timeout
- lost response
- output decode failure
- output schema failure
- local persistence failure

で外部結果が不明なら、

```text
FAILED
```

ではなく、

```text
WAITING_RECONCILIATION
```

へ進める。

## 6.4 Transaction

`commit_transition()`等が存在するだけでは不十分。

Controllerのcritical transitionから実際に利用し、

fault testでatomicityを証明する。

## 6.5 Audit

Eventへ生payloadを無制限保存しない。

最低限、

- secret redaction
- total byte cap
- large content handling
- append-only event

を実装。

## 6.6 Crash Matrix

最低境界:

```text
before model
model request
model response
pending tools
before Tool
after Tool
ToolResult
after-tools
waiting approval
waiting reconciliation
failure
after-model
terminal event
```

---

# 7. Phase 4 補助条件

対応FND:

```text
FND-15 Provider-neutral Adapter
FND-16 Provider Error Taxonomy
FND-17 Provider Contract Qualification
```

## 外してはいけない条件

「Local ProviderへHTTP接続できた」だけではGate PASSにしない。

最低限、

```text
text
model-generated ToolCall
ToolResult return
final response
```

を実モデルで完走。

## モデル適格性

Provider Adapterが正しくてもModel自体が不適格な場合がある。

例:

- reasoningでoutput budgetを使い切る
- tool selectionが不安定
- visible answerを返さない
- schema follow率が低い

ProviderとModel qualificationを分ける。

---

# 8. Phase 5 補助条件

対応FND:

```text
FND-15
FND-16
FND-17
```

## 外してはいけない条件

複数Providerがあることではなく、

> Coreを変えずにProviderを交換できること

を証明する。

## Provider Error

最低分類:

```text
transport
authentication
authorization
rate_limit
quota
provider_http
provider_decode
context_limit
output_limit
unsupported_capability
```

Router実装前にこの分類を安定させる。

## Capability Matrix

観測値は永久真実ではない。

最低限、

```text
model
API version
adapter version
tested_at
confidence
known quirks
```

を持つ。

---

# 9. Phase 6 補助条件

対応FND:

```text
FND-18 Resource Ledger
FND-19 Budget Governor
FND-20 Model / Resource Router
FND-21 Survival Modes
FND-22 Scheduler / Queue
FND-23 Worker Concurrency
FND-29〜31 Recovery
```

Phase 6は内部的には分割して扱う方がよい。

## Phase 6A — Resource / Budget

### 必須

Resourceごとにnative unitを保持。

```text
token
request
GPU time
yen
subscription allowance
```

等を無理に一単位へ統合しない。

Paid call前にbudget reservation。

## Phase 6B — Router / Survival

Router判断:

```text
capability
health
quota
cost
latency
privacy
```

Privacy/permissionはcostより優先。

Provider flapping防止のため、

```text
cooldown
circuit breaker
hysteresis
```

を検討。

## Phase 6C — Recovery

最低限:

- independent diagnosis
- backup
- restore
- maintenance lock
- last-known-good
- rollback
- repair branch
- test invocation

### 注意

`backup()`があるだけで「backup/restore PASS」にしない。

## Phase 6D — Scheduler / Worker Ownership

Schedulerを導入するなら必須。

```text
durable queue
lease_owner
lease_until
state_version
```

複数Workerが同じTaskを実行しないこと。

Multi-Agent以前でもbackground executionを始めた時点で必要。

---

# 10. Phase 7 補助条件

Phase 7の詳細は別紙、

> `dev_agent 自律改善・自己進化システム 目標段階定義`

を正本とする。

本資料では重複定義しない。

ただしPhase 7開始前に、下位基盤として最低限、

```text
Recovery restore
rollback
last-known-good
exact HEAD CI
protected tests
Tool containment
Audit safety
```

が必要。

---

# 11. Phase 8 補助条件

## Multi-Agent開始前

以下を先に完成:

```text
durable task ownership
structured handoff
handoff limit
cycle detection
per-role budget
memory scope
```

自由会話だけでAgent間協調させない。

## AI Company開始前

Human Authorityを明示。

最低限Human Approval対象:

```text
payment
new paid subscription
external publication
contract
credential
irreversible delete
high-risk change
```

Revenue objectiveより、

```text
policy
legal
budget
reputation
human authority
```

を上位制約とする。

---

# 12. Phase 9 補助条件

## UI原則

UIはStateの正本ではない。

```text
Durable Runtime
↓
API
↓
UI
```

を維持。

## Approval UI

最低表示:

```text
Task
Tool
effective arguments
destination
cost
side effect
approval scope
```

UIで承認したpayloadとRuntime実行payloadをhash等で一致確認できるようにする。

---

# 13. 横断Trust Boundary

ロードマップ全体で以下を意識する。

```text
User → Agent
External Data → Agent
Model → Controller
Controller → Tool
Tool → External Service
Provider → Core
Runtime → State
State → Recovery
Recovery → System
Agent → Git
Agent → Agent
UI → Runtime
```

各境界で最低、

```text
入力
信頼度
権限
validation
failure
audit
```

を定義する。

---

# 14. 前倒しして仕様だけ固定しておく条件

現時点で実装は不要でも、後の手戻り防止のため先にContractだけ決める。

## Scheduler前

- Task lease
- state version
- queue ownership

## External data導入前

- trust provenance
- prompt injection boundary

## Self-repair前

- protected files/tests
- repair branch
- rollback

## Generated Tool前

- subprocess isolation
- dependency policy
- filesystem/network sandbox

---

# 15. 後段へ延期してよいもの

現在Phase 3.5へ持ち込まない。

```text
Resource Ledger
Budget Router
Survival Mode
Scheduler
Multi-Agent
Self-Repair
Self-Extension
Workflow Promotion
AI Company
Virtual Office UI
```

ただし、それらの前提を壊す設計を今入れない。

---

# 16. Current GateとFoundationの対応

大分類として:

```text
Stage A
→ Tool / Approval / Effect / Reconciliation

Stage B
→ State / Migration / Transaction / Audit

Stage C
→ Limits / Cancellation / Isolation / Task expansion

Stage D
→ Provider Contracts

Stage E
→ Recovery / CI / Governance
```

`GATE_STATUS.json`は、この補助資料のうち**現在Phaseで検証するsubset**として扱う。

---

# 17. Gate Status運用上の注意

推奨状態:

```text
TODO
IMPLEMENTED
INTEGRATED
VERIFIED
BLOCKED
DEFERRED
```

または内部Evidenceとして同等の段階を保持。

外向き`PASS`は`VERIFIED`のみ。

---

# 18. ロードマップ進行時の判定順

各Phase終了時に以下を確認する。

```text
1. Required behavior implemented?
2. Runtime path integrated?
3. Normal test pass?
4. Boundary/fault test pass?
5. Recovery behavior defined?
6. Evidence exact HEAD?
7. Open P0/P1 zero?
8. Current State docs synced?
```

すべてYESで次へ進む。

---

# 19. 現在の適用範囲

現在Phase 3.5では主に、

```text
Tool safety
Cancellation
Crash consistency
Transaction
Approval
External Effects
Reconciliation
Migration
Audit
Gate evidence
```

だけへ集中する。

Phase 6以降の実装を混ぜない。

---

# 20. 本資料の使い方

ロードマップ本体:

> 次にどこへ進むかを見る。

本資料:

> そのPhaseで何を落としてはいけないかを見る。

Foundation Requirements:

> 条件の厳密な定義を確認する。

Self-Improvement Target:

> 通常基盤完成後の自律改善目標を確認する。

関係:

```text
現行ロードマップ
      ↓
本補助資料
      ↓
Foundation Requirements
      ↓
実装 / Test / Gate Evidence

通常基盤完成
      ↓
Self-Improvement Target
```
