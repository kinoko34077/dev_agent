# dev_agent 自律改善到達前・基盤完成条件定義

Status: TARGET / FOUNDATION REQUIREMENTS  
Scope: 自律改善・自己進化機構を成立させるための下位基盤  
Relationship: 「dev_agent 自律改善・自己進化システム 目標段階定義」の下位仕様

---

# 0. 本書の役割

本書は、

> dev_agentが将来自律改善・自己修復・自己拡張・Workflow化を安全に実行するため、それ以前に基盤側で完成していなければならない条件

のみを定義する。

以下は上位目標表で既に定義済みのため、本書では再定義しない。

- Self-Observation
- Self-Diagnosis
- Improvement Planning
- Repair Candidate Generation
- Automated Validation
- Controlled Self-Repair
- Self-Extension
- Agent → Workflow Promotion
- Continuous Improvement Loop
- 自律改善固有のRisk分類
- 自律改善専用Limit
- 自律改善固有の昇格条件
- Protected Coreの自己変更規則
- 自律改善の最終KPI

本書はあくまで、それらが依存する**通常dev_agent基盤の完成条件**を扱う。

---

# 1. 基盤完成の定義

基盤は「機能が存在する」だけでは完成としない。

各要件は原則として、

```text
Specified
↓
Implemented
↓
Integrated
↓
Verified
↓
Fault-Verified
```

まで到達して初めて完成とする。

関数・class・設定項目の存在だけではPASSとしない。

---

# 2. FND-01 — Protocol / Domain Model

## 目的
Provider、Tool、State、Runtime、Recovery等が同じ内部契約を共有し、外部実装差をCoreへ漏らさない。

## 必須条件

内部Domain Modelとして最低限、

```text
Task
Step
ModelRequest
ModelResponse
ToolCall
ToolResult
Event
ExecutionLimits
```

の契約が固定されていること。

さらに、

- Provider固有SDK objectをCoreへ入れない。
- Provider固有IDと内部IDを分離する。
- serialization / deserializationが対称。
- unknown fieldの扱いを固定。
- enum追加・削除時の互換規則を固定。
- timestamp表現を統一。
- ID生成責任を明確化。
- 金額・quota・token等の単位を曖昧にしない。
- エラーを自由文だけで扱わない。

## 完成条件
古い保存fixture・異常fixture・複数Provider fixtureを同じ内部契約へ正規化できる。

---

# 3. FND-02 — Deterministic Runtime Control

## 目的
Agent実行を有限・再現可能・停止可能な状態機械として成立させる。

## 必須条件

Runtimeは再帰的control flowを使用せず、明示的な反復状態機械とする。

最低限強制するもの:

```text
max_steps
max_model_calls
max_tool_calls
wall-clock deadline
model-call deadline
tool-call deadline
total task expansion
argument/result size
```

採用しないLimit項目は仕様から除外または`DEFERRED`とする。

「定義はあるが効かない安全機構」を残さない。

## 必須異常系

- Provider無応答
- Tool無応答
- malformed response
- unknown Tool
- Tool exception
- cancellation
- deadline超過
- limit超過

がすべて明示状態へ収束すること。

---

# 4. FND-03 — Cancellation Semantics

## 目的
「止めた」と判定した処理が裏で生存し続ける状態を防ぐ。

## 必須条件

Cancellationを、

```text
requested
acknowledged
terminated
unable_to_confirm
```

等の明示状態として扱う。

Threadの`future.cancel()`だけをhard terminationとみなさない。

実行方式ごとに停止方法を定義する。

```text
trusted in-process
subprocess
external network operation
long-lived worker
```

Cancellation後の副作用発生有無を検証可能であること。

---

# 5. FND-04 — Durable State SSOT

## 目的
process memoryではなく、永続Stateを実行状態の正本とする。

## 必須条件

Production用途のState backendを一つ明確にSSOT指定する。

永続対象:

- Task
- Step
- ToolResult
- checkpoint
- Event
- approval
- idempotency record
- external effect state
- reconciliation evidence

RAM上cache/projectionを正本扱いしない。

JSON等の補助Storeを残す場合は用途境界を明示する。

---

# 6. FND-05 — Schema Version / Migration

## 目的
古いStateを新しいRuntimeでも安全に扱えるようにする。

## 必須条件

State schemaには一意なversionを持つ。構造変更ごとにversionを更新。

Migrationは、

```text
N
→ N+1
→ N+2
```

の明示chainとする。

禁止:

```text
古いDB
↓
単にschema_versionだけ最新へ書換え
```

## Migration必須試験

- old fixture → latest
- migration再実行
- migration中crash
- future unsupported version
- partial schema
- malformed payload

Migration完了後にsemantic validationを行う。

---

# 7. FND-06 — Transaction Boundary

## 目的
Task状態とAudit状態が互いに矛盾しないようにする。

## 必須条件

一つのlogical transitionに属する、

```text
Task
Step
Checkpoint
ToolResult
Event
```

を可能な範囲で一transactionにまとめる。

特に、

- terminal completion
- failure
- waiting state
- ToolResult確定
- approval consumption
- external effect claim

のatomicityを明示する。

「repairで直せるから非atomicでよい」を通常設計にしない。

---

# 8. FND-07 — Crash Consistency

## 目的
任意の永続化境界でprocessが消えても、不正な再実行を起こさない。

## 必須Crash Matrix

```text
before model
model request後
model response後
pending tools保存後
各Tool開始前
各Tool終了直後
ToolResult保存前後
after-tools
waiting state
failure
after-model
terminal event前後
migration中
```

unit-level例外だけではなく、

```text
subprocess
os._exit
process kill
```

等の実process deathも含める。

---

# 9. FND-08 — Idempotency Ownership

## 目的
ModelやProviderへ再実行同一性の判断を委ねない。

## 必須条件

operation identityはKernelが生成・管理する。

Binding:

```text
operation_id
task_id
step/order
call_id
tool_name
canonical arguments
```

同一operation IDに異なるargumentsが現れた場合はcache hitではなくintegrity violation。

外部APIがidempotency keyを提供する場合、内部operation IDとの対応を保存する。

---

# 10. FND-09 — Tool Contract

## 目的
Toolを単なるPython callableではなく制御可能な実行単位として扱う。

## ToolSpec最低項目

```text
name
version
description
input schema
output schema
side-effect class
timeout
argument/result limits
permission requirements
path requirements
idempotency policy
```

## 必須条件

- Runtimeが入力schemaを検証。
- Handler outputもschema検証。
- Providerへ見せるschemaとRuntimeが強制するschemaを一致させる。
- Runtime未対応schema keywordを登録時に拒否。
- handler implementation referenceをModelへ露出しない。
- disabled Toolをfail closed。

---

# 11. FND-10 — Prepared Tool Call

## 目的
Modelが提案した内容と実際に実行される内容の差をなくす。

Tool実行前に、

```text
raw ToolCall
↓
schema validation
↓
permission evaluation
↓
path normalization
↓
argument normalization
↓
effective payload
↓
PreparedToolCall
```

を確定する。

以後、

- approval
- operation identity
- audit
- external dispatch

はRaw CallではなくPrepared Callを基準にする。

---

# 12. FND-11 — Filesystem Boundary

## 目的
Toolによる意図しないworkspace外アクセスを防ぐ。

## 必須条件

- traversal拒否
- symlink escape拒否
- resolved path validation
- read/write/execute権限分離
- workspace capability単位管理
- platform差検証

将来的な考慮対象:

- Windows junction/reparse
- UNC
- ADS
- Unix device/FIFO/socket
- TOCTOU

Path文字列検査だけで安全性を主張しない。

---

# 13. FND-12 — Human Approval Runtime

## 目的
高Risk副作用を実行前に人間の権限へ戻す。

## Approval binding

```text
approval_id
task_id
call_id
tool_name
effective arguments hash
side-effect class
actor
issued_at
expiry
consumption state
revocation state
```

Approvalは原則one-shot。

別call、別arguments、別Taskへの転用不可。

Consumption時点の有効性判定と消費をatomicに行う。

---

# 14. FND-13 — External Effect State Machine

## 目的
「失敗」と「外部結果不明」を区別する。

外部副作用を通常Tool Resultだけで管理しない。

最低状態:

```text
PREPARED
DISPATCHING
SUCCEEDED
CONFIRMED_FAILED
UNKNOWN
RECONCILING
```

必要に応じて、

```text
RECONCILED
COMPENSATED
```

等を追加。

## 重要Invariant

dispatch後に結果確定不能なら通常failureへ落とさない。

以下を区別する。

```text
送信前失敗
確実な拒否
成功確認
成功した可能性あり
```

---

# 15. FND-14 — Reconciliation Contract

## 目的
UNKNOWN状態を「人間がDBを直接直す」以外の方法で解消する。

Reconciliation record最低項目:

```text
operation_id
actor
source
external_id
checked_at
result
evidence
```

外部サービスによって照会不能な場合、その制約をTool contractへ記録する。

UNKNOWN状態での自動再送禁止。

---

# 16. FND-15 — Provider-Neutral Adapter Boundary

## 目的
特定ProviderのSDK・仕様変更でKernelを壊さない。

Provider adapter責務:

```text
internal request
↓
provider-specific payload
↓
provider
↓
provider-specific response
↓
internal response
```

KernelはProvider transport詳細を知らない。

---

# 17. FND-16 — Provider Error Taxonomy

## 目的
Router・Retry・Recoveryが失敗原因を判断できるようにする。

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
safety_block
unsupported_capability
```

ProviderErrorは最低、

```text
category
retryable
provider
model
http_status
retry_after
```

を必要に応じて保持する。

自由文解析はfallbackだけにする。

---

# 18. FND-17 — Provider Contract Qualification

## 目的
Providerの能力を想像ではなく観測結果として扱う。

最低試験:

```text
text
single tool
multi-tool
sequential tool
tool-result roundtrip
malformed
timeout
401
403
429
quota
context limit
output limit
```

Capability evidenceには、

```text
provider
model
API version
adapter version
tested_at
expires_at
confidence
known quirks
```

を保存。

一度成功しただけで永久PASSにしない。

---

# 19. FND-18 — Resource Ledger

## 目的
利用可能な計算資源を統一的に管理する。ただし異なる資源を無理に同一単位へ変換しない。

扱う例:

```text
Local compute
Free API quota
Paid API
Subscription-limited model
External service
```

各Resource stateには最低、

```text
resource_id
native unit
available/estimated capacity
cost model
source
observed_at
confidence
health
```

を保持する。

---

# 20. FND-19 — Budget Governor

## 目的
Agent自身の判断だけで支出上限を突破できないようにする。

必須:

- reserve before paid call
- actual usage reconciliation
- concurrent reservation
- unknown price fail-closed
- monthly hard cap
- recovery reserve隔離
- budget state persistence

金銭計算はbinary float前提にしない。

---

# 21. FND-20 — Model / Resource Router

## 目的
Task要求に合うResourceを安全に選択する。

判断軸:

```text
required capability
health
availability
cost
latency
privacy
quota
known provider quirks
```

Privacy/permissionはcostより上位制約。

Fallback loop防止として、

```text
circuit breaker
cooldown
hysteresis
```

等を持つ。

---

# 22. FND-21 — Survival Modes

## 目的
Resource枯渇やProvider障害時にも安全な縮退運転を可能にする。

最低状態:

```text
NORMAL
CONSERVE
SURVIVAL
```

各modeで、

- 許可Provider
- 使用可能Tool
- budget threshold
- retry policy
- quality/latency tradeoff

を明示する。

Mode transitionをLLMの自由判断にしない。

---

# 23. FND-22 — Scheduler / Queue Durability

## 目的
将来自律処理を継続運転してもTaskを重複・消失させない。

必要:

```text
durable queue
task ownership
claim
lease
lease expiry
retry state
scheduled time
priority
```

process restart後にqueue状態を復元できる。

---

# 24. FND-23 — Worker Concurrency Control

## 目的
複数Workerが同じTaskを二重実行しない。

最低概念:

```text
lease_owner
lease_until
state_version
```

または同等のatomic claim方式。

必須試験:

```text
Worker A / Worker B
simultaneous claim
→ exactly one owner
```

外部effect claimとも競合整合性を持たせる。

---

# 25. FND-24 — Task Graph Durability

## 目的
Task relationshipをRAM上だけに保持しない。

親子・依存関係は永続Stateから再構築可能であること。

最低制約:

```text
max depth
max children
max total tasks per root
cycle prevention
```

将来DAG依存を導入する場合はparent-chain cycle検査とは別に定義する。

---

# 26. FND-25 — Event / Audit Contract

## 目的
後から「何が起きたか」を再構築可能にする。

Event最低項目:

```text
event_id
event_type
task_id
step_id
timestamp
actor/provider
payload metadata
```

Event ID重複は上書きしない。

重要transitionには対応するAudit evidenceが存在すること。

---

# 27. FND-26 — Sensitive Data Boundary

## 目的
State/Event/Promptへ秘密情報を無制限保存しない。

最低限:

- key-based redaction
- value-pattern detection
- total payload size limit
- large content truncation
- secret-bearing artifact separation
- retention policy

CredentialsそのものをAudit evidenceとして保存しない。

---

# 28. FND-27 — Trust / Provenance Labels

## 目的
外部データを権限ある命令として誤認しない。

最低分類例:

```text
trusted_system
trusted_user
trusted_internal
untrusted_external
```

特に、

- Web
- Email
- GitHub Issue
- Slack
- uploaded documents
- external API responses

は原則untrusted external dataとして扱う。

Data provenanceをToolResult/Eventへ保持可能にする。

---

# 29. FND-28 — Prompt Injection Boundary

## 目的
外部コンテンツ中の命令がPolicyを乗っ取らないようにする。

必須原則:

```text
external content
≠
authorized instruction
```

Tool permissions・approval・budget・credential accessはLLM contextだけで変更不能。

---

# 30. FND-29 — Recovery Plane Independence

## 目的
Normal Runtimeが壊れた際にも診断経路を残す。

Recovery planeは可能な限り、

- Provider-independent
- normal Runtime-independent
- minimal dependency
- offline-capable

とする。

RecoveryがNormal Runtimeと同じfailure dependencyを持たないこと。

---

# 31. FND-30 — Maintenance Exclusivity

## 目的
Recovery中と通常運転中が同時にStateを書換えるsplit-brainを防ぐ。

Maintenance mode/lockを持つ。

Recovery処理開始前に通常Worker ownershipを停止・失効させられること。

---

# 32. FND-31 — Recovery Diagnostics

## 目的
「壊れた」ではなく壊れた層を特定できるようにする。

独立検査対象:

```text
runtime environment
Git state
configuration
State DB
schema/migration
Provider connectivity
test health
filesystem
```

診断自体はStateを修正しない。

---

# 33. FND-32 — Repository Baseline

## 目的
新Kernelとlegacyを混同しない。

必須:

```text
main/current baseline
legacy frozen reference
v2 development line
```

の役割を固定。

v2 Runtimeがlegacy Runtimeをimportしない。

Legacyはfixture/evidenceとしてのみ使用可能。

---

# 34. FND-33 — Dependency Boundary

## 目的
旧Provider SDKや不要dependencyの障害がKernel全体を壊さない。

分離対象:

```text
v2 core
legacy replay
provider-specific optional dependencies
development/test dependencies
```

Core起動に特定Cloud Provider SDKを必須化しない。

---

# 35. FND-34 — Specification Traceability

## 目的
「何のためのコードか」を後から追跡可能にする。

最低追跡:

```text
Invariant
→ Requirement
→ implementation
→ test
→ evidence
```

仕様・実装・テストのいずれかだけが更新されない状態を検知可能にする。

---

# 36. FND-35 — Machine-readable Gate Governance

## 目的
進捗を人間の印象ではなく機械的条件で管理する。

Gate status:

```text
TODO
BLOCKED
PASS
```

等をmachine-readableに保持。

ただしPASSは、

```text
implemented
+
integrated
+
verified
```

された場合のみ。

「関数が存在する」だけのEvidenceをPASS根拠にしない。

外部依存で試験不能なら`BLOCKED`でありPASSではない。

---

# 37. FND-36 — Gate Evidence Integrity

## 目的
Gateと実際のコードが乖離しないようにする。

Evidenceには可能な限り、

```text
immutable commit SHA
test identifier
artifact/report
observed date
environment
```

を結び付ける。

古いHEADのtest結果を現在HEADの証拠として使わない。

---

# 38. FND-37 — CI Reproducibility

## 目的
開発機固有の成功を排除する。

最低限、

- clean checkout
- isolated dependencies
- deterministic setup
- kernel tests
- compile/static validation

が自動実行できること。

OS依存機能は対象OSで別検証。

---

# 39. FND-38 — Fault Injection Baseline

## 目的
通常testだけでは見つからない分散状態・部分成功を継続検査する。

常設Faultカテゴリ:

```text
process crash
timeout
network failure
provider malformed response
DB lock
DB corruption fixture
schema mismatch
concurrent execution
external outcome ambiguity
permission denial
resource exhaustion
```

修正後もfixtureを削除せず回帰試験として保持。

---

# 40. FND-39 — Error Semantics

## 目的
単なるException名ではなく、運用上の意味で失敗を分類する。

最低区別:

```text
terminal failure
retryable failure
blocked
waiting approval
waiting reconciliation
cancelled
timeout
resource unavailable
policy denied
integrity violation
```

「何でもFAILED」に集約しない。

---

# 41. FND-40 — Current State Documentation

## 目的
実装済み・未実装・Blockedを混同しない。

各subsystemについて、

```text
Implemented
Integrated
Verified
Blocked
Deferred
```

を明示。

`offline verified`と`live verified`を区別。

過去に正しかった文書でも現HEADとズレた場合はCurrent Stateとして使わない。

---

# 42. 基盤完成チェック

本書の基盤完成は、大分類として以下がすべて閉じた状態を指す。

```text
Protocol
Runtime Control
Cancellation
Durable State
Migration
Transactional Persistence
Crash Consistency
Idempotency
Tool Contract
Prepared Execution
Filesystem Boundary
Approval
External Effects
Reconciliation
Provider Boundary
Provider Qualification
Resource Ledger
Budget
Router
Survival
Scheduler
Concurrency
Task Graph
Audit
Sensitive Data
Trust Boundary
Recovery Plane
Repository Governance
Specification Traceability
CI
Fault Injection
Error Semantics
Current State Governance
```

---

# 43. 完成後の状態

本書の要件が完成するとdev_agentは、

```text
壊れにくい
止められる
戻れる
再開できる
二重実行しにくい
外部副作用を曖昧なまま再送しない
Provider停止へ耐えられる
Resourceを有限管理できる
自分の状態を正確に記録できる
```

通常実行基盤になる。

その上に、別紙「dev_agent 自律改善・自己進化システム 目標段階定義」で定義した自律改善能力を載せる。

---

# 44. 設計上の最終原則

下位基盤では、

> 「賢く判断できること」より先に  
> 「状態を間違えないこと」を完成させる。

優先順位:

```text
Correctness
↓
Durability
↓
Safety
↓
Recoverability
↓
Observability
↓
Resource Control
↓
Autonomy
```

自律改善機構は、この順序を逆転させない。
