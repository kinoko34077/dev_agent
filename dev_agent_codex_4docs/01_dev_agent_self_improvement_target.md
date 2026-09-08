# dev_agent 自律改善・自己進化システム 目標段階定義

Status: TARGET / ROADMAP  
Scope: Self-Observation / Self-Diagnosis / Self-Repair / Self-Extension / Workflow Promotion  
Purpose: dev_agent自身が運用結果・障害・不足能力・コスト・成功パターンを観測し、安全かつ検証可能な方法で継続的に改善できる状態を最終目標として定義する。

---

# 0. 最終目標

dev_agentは最終的に以下の閉ループを自律実行できること。

```text
Observe
↓
Detect
↓
Diagnose
↓
Plan
↓
Propose
↓
Modify / Extend
↓
Test
↓
Fault Inject
↓
Compare
↓
Promote or Reject
↓
Observe again
```

ただし、

```text
LLMが自分を直接書き換える
```

構造にはしない。

原則:

```text
自律改善
=
観測
+ 診断
+ 改善候補生成
+ 隔離変更
+ 客観検証
+ 昇格判定
+ rollback可能性
```

---

# 1. 基本原則

## SI-001 Evidence First

改善理由は必ず機械可読なEvidenceに基づく。

候補:

- Gate Status
- Runtime Event
- Test Result
- Fault Injection Result
- Provider Contract Report
- Provider Capability Matrix
- Resource Ledger
- Budget Result
- Recovery Diagnosis
- Benchmark
- User/Human Feedback
- Task Success/Failure Trace

「LLMがなんとなく良いと思った」は改善理由にならない。

## SI-002 Propose Before Modify

必ず、

```text
Finding
↓
ImprovementPlan
↓
Modification
```

の順にする。

診断から直接Coreを書き換えない。

## SI-003 Branch Isolation

自己修正は必ず隔離された変更領域で行う。

原則:

```text
main/current
↓
repair/improvement branch
↓
patch
↓
validation
↓
promotion
```

実行中の正常系コードを直接書換えない。

## SI-004 Validation Before Promotion

変更後に最低限、

- unit test
- integration test
- regression
- invariant test
- relevant fault injection
- old/new comparison

を行う。

PASSしていない変更は有効化しない。

## SI-005 Rollback Before Autonomy

自律修正より先に、

- backup
- restore
- last-known-good
- rollback
- repair branch
- recovery path

が成立していなければならない。

## SI-006 Protected Core

以下は通常の自己改善対象から除外する。

- Recovery root
- Hard Budget upper limit
- Credential Policy
- Root permissions
- Audit disable controls
- Human approval requirements
- Protected invariant tests
- Security tests
- Recovery tests
- Self-modification protection policy

変更にはHuman Approval必須。

## SI-007 Risk-tiered Autonomy

全変更を同じ自律度にしない。

### LOW

例:

- documentation
- non-critical prompt
- harmless workflow optimization
- Provider metadata
- test fixture補充

条件を満たせば将来自動昇格可能。

### MEDIUM

例:

- Tool
- Workflow
- Provider adapter
- normal Runtime component

自動修正可でも厳格な検証を要求。

### HIGH

例:

- Kernel control flow
- permission
- budget
- credentials
- recovery
- external-effect safety
- audit
- approval

Human Approval必須。

---

# 2. 全体段階

```text
Phase 3.5 / Recovery基盤完成
        ↓
F0 Self-Observation
        ↓
F1 Self-Diagnosis
        ↓
F2 Improvement Planning
        ↓
F3 Repair Candidate Generation
        ↓
F4 Automated Validation
        ↓
F5 Controlled Self-Repair
        ↓
F6 Self-Extension
        ↓
F7 Agent → Workflow Promotion
        ↓
F8 Continuous Improvement Loop
```

各StageはAND Gate。一条件でも未達なら次Stageへ進まない。

---

# F0 — Self-Observation

## 目的
dev_agentが自分自身の状態を機械的に観測可能にする。まだ判断・修正はしない。

## 入力

最低限:

```text
GATE_STATUS
Runtime Events
Task State
Test Results
Recovery Diagnostics
Provider Contract Reports
Capability Matrix
```

将来:

```text
Resource Ledger
Budget Ledger
Revenue Ledger
Performance Metrics
Human Feedback
```

## 出力

標準化されたObservation。

```text
Observation
- source
- category
- observed_at
- subject
- value
- expected
- severity_hint
- evidence_ref
```

## 必須条件

- Evidenceを読み取れる。
- raw dataと推論を分離する。
- timestamp/source/provenanceを保持する。
- Observation自体はsystemを書換えない。
- 観測不能を成功扱いしない。
- stale evidenceを区別できる。

## 完了Gate

```text
複数Evidence source
→ standardized Observation
→ persistent record
```

がLLMなしでも成立。

---

# F1 — Self-Diagnosis

## 目的
Observationから問題・異常・能力不足をFindingへ変換する。

## 出力

```text
ImprovementFinding
```

最低フィールド:

```text
finding_id
category
severity
subject
summary
evidence_refs
affected_invariants
affected_requirements
confidence
suspected_causes
blocked_by
created_at
```

## Findingカテゴリ例

- defect
- regression
- reliability
- security
- performance
- cost
- provider degradation
- capability gap
- workflow inefficiency
- recovery weakness
- specification drift

## 必須条件

- EvidenceなしFinding禁止。
- FactとHypothesisを区別。
- 同一問題のduplicate detection。
- stale finding管理。
- severity rule。
- P0/P1は明示的に昇格。
- Provider障害とKernel障害を混同しない。

## 完了Gate
既知のfault fixtureに対して期待したFindingを生成できる。

---

# F2 — Improvement Planning

## 目的
Findingを、安全に実施可能な改善計画へ変換する。

## 出力

```text
ImprovementPlan
```

最低フィールド:

```text
plan_id
finding_ids
objective
scope
affected_components
proposed_changes
acceptance_tests
fault_tests
rollback_conditions
risk_class
required_permissions
required_human_approval
estimated_cost
dependencies
status
```

## 必須条件

改善計画に必ず、

```text
何を直すか
何を変えないか
どう成功判定するか
どう失敗判定するか
どう戻すか
```

を含める。

## 禁止

- acceptance testなしの変更計画
- rollback条件なし
- Protected Core変更をLOW扱い
- 「全面書換え」を無根拠に選択

## 完了Gate
Findingから再現可能なPlanを生成し、人間/CodexがそのPlanだけで実装可能。

---

# F3 — Repair Candidate Generation

## 目的
ImprovementPlanに従い、実際の変更候補を隔離生成する。

## 流れ

```text
ImprovementPlan
↓
create repair branch
↓
inspect affected code
↓
minimal patch
↓
candidate artifact
```

## 必須条件

- current/main直接変更禁止。
- branch/working-copy隔離。
- Plan外変更禁止。
- Protected file変更検知。
- diff size記録。
- dependency追加を明示。
- generated code provenance保持。
- 変更前commit保持。

## 出力

```text
RepairCandidate
- plan_id
- branch
- base_commit
- candidate_commit/diff
- changed_files
- dependency_changes
- protected_area_changes
```

## 完了Gate
意図的なbug fixtureに対し、branch上だけに修正候補を生成できる。

---

# F4 — Automated Validation

## 目的
変更候補を自動評価する。

## 検証順序

```text
1 static/schema validation
2 unit tests
3 integration tests
4 regression tests
5 invariant tests
6 security tests
7 relevant fault injection
8 recovery tests
9 benchmark
10 old/new comparison
11 optional LLM review
```

決定論検査をLLM評価より先に行う。

## 出力

```text
ValidationReport
```

最低フィールド:

```text
candidate_id
tests_run
tests_passed
tests_failed
invariant_results
fault_results
security_results
old_metrics
new_metrics
regressions
risk
recommendation
```

## 必須条件

- test skipをpass扱いしない。
- tests自体の変更を検出。
- Protected Testsの変更禁止。
- benchmark悪化検出。
- new warningを記録。
- validation environmentを記録。

## 完了Gate

- 良い修正
- regressionする修正
- testだけ弱体化する修正

を投入し、後者2つを拒否できる。

---

# F5 — Controlled Self-Repair

## 目的
低〜中Riskの既存機能修正を、監督付きで自律完結させる。

## Flow

```text
Observe
↓
Diagnose
↓
Plan
↓
Repair Branch
↓
Patch
↓
Validate
↓
Promotion Request
↓
Human Approval
↓
Merge
↓
Post-merge validation
↓
Monitor
```

初期段階ではmergeはHuman Approval必須。

## 必須条件

- Recovery全部PASS。
- rollback実演済み。
- exact-head CI。
- protected tests。
- branch protection。
- failure時自動rollback候補。
- repair自体のBudget上限。
- max repair attempts。
- repair loop検出。

## 禁止

```text
repair failed
→ repair repair
→ repair repair repair
→ ...
```

の無限自己修復。

必ず`max_repair_attempts`を持つ。

## 完了Gate

```text
detect
→ repair branch
→ fix
→ tests
→ approval
→ restore
```

まで完走。

---

# F6 — Self-Extension

## 目的
既存機能の修正ではなく、新能力を自分で追加する。

## Capability Gap

```text
Task requires PDF extraction
but no compatible Tool exists
↓
CapabilityGap
```

## Flow

```text
CapabilityGap
↓
Capability Spec
↓
Tool/Workflow candidate
↓
implementation
↓
isolated validation
↓
security validation
↓
candidate registry
↓
activation
```

## 新Tool最低仕様

```text
name
version
description
input_schema
output_schema
permissions
side_effect_level
timeout
resource limits
network policy
filesystem policy
idempotency policy
approval policy
dependency manifest
provenance
```

## 必須条件

- generated codeは隔離実行。
- arbitrary dependency install禁止。
- dependency allowlist。
- network default deny。
- filesystem default deny。
- CPU/RAM/process limit。
- process tree kill。
- Tool自身がRegistry/Policyを直接変更不可。
- security tests必須。
- disable/revoke可能。

## 完了Gate
未知Capability Gapから新Tool候補を生成し、安全検証後に登録候補まで進める。

---

# F7 — Agent → Workflow Promotion

## 目的
繰り返し成功するAgent推論を、安価・高速・安定な汎用機構へ変換する。

原則:

```text
知能処理を機構へコンパイルする
```

## Flow

```text
Unknown Task
↓
Agent solves
↓
successful trace
↓
Pattern Detection
↓
Workflow Candidate
↓
Generalization
↓
Representative Tests
↓
Fault Tests
↓
Agent baseline comparison
↓
Promotion
```

## Promotion条件

- 複数成功例。
- 安定した入出力。
- variation axisが特定可能。
- side effectが理解可能。
- success criterionが測定可能。
- representative test作成可能。
- Agentより同等以上の成功率。
- cost/latencyの改善またはstability改善。

単純に「N回成功したら昇格」と固定しない。

## Workflow種類

```text
Fixed Workflow
Parameterized Workflow
Open-ended Agent
```

目標:

```text
未知 → Agent
既知化 → Parameterized Workflow
完全定型 → Fixed Workflow
```

## 完了Gate

```text
成功率維持
+
LLM call減少
+
cost減少
or
latency減少
```

を実証。

---

# F8 — Continuous Improvement Loop

## 目的
F0〜F7を連結し、継続改善を通常運用へ統合する。

## 完全Loop

```text
Task execution
↓
Events / Metrics
↓
Observer
↓
Finding
↓
Improvement Plan
↓
Repair / Extension / Workflow Candidate
↓
Validation
↓
Promotion
↓
Production
↓
Post-Promotion Observation
↓
Regression?
 ├ Yes → rollback / repair
 └ No  → continue
```

## 必須条件

- improvement taskも通常Taskとして管理。
- improvement自身にもBudget。
- improvement自身にもStep limit。
- improvement自身にもProvider routing。
- improvement自身にもAudit。
- improvement自身にもHuman Authority。
- improvement loop自身の停止条件。
- improvementによる性能悪化を検出。
- rollback後同じ変更を無限提案しない。

## 完了Gate

- invariant違反0
- rollback成功
- regression検出
- duplicate proposal suppression
- budget遵守

を複数cycleで実証。

---

# 3. 横断コンポーネント

```text
src/dev_agent/improvement/

observer/
  observer.py

diagnosis/
  finding.py
  diagnostician.py

planning/
  plan.py
  planner.py

repair/
  candidate.py
  branch_manager.py

validation/
  validator.py
  comparator.py

extension/
  capability_gap.py
  tool_candidate.py

promotion/
  policy.py
  workflow_promotion.py

models/
  observation.py
  finding.py
  plan.py
  validation_report.py
```

実際のdirectory構造は実装時ADRで確定する。

---

# 4. 自律改善が参照する正本

```text
1 Current accepted specification
2 Invariants
3 Gate definitions
4 Actual current code
5 Actual tests/results
6 Runtime observations
7 Provider observations
8 Improvement hypotheses
```

LLM推論が仕様を上書きしない。

---

# 5. 自律改善が変更してよい領域

初期ALLOW候補:

```text
Provider adapters
Tools
Workflows
Prompts
Non-protected configuration
Documentation
Tests excluding protected invariant/security tests
```

初期DENY:

```text
Recovery root
Budget hard cap
Approval authority
Credential access policy
Audit protection
Root permission policy
Protected tests
Main branch protection
Self-modification policy itself
```

---

# 6. 自律改善専用Limit

```text
max_findings_per_cycle
max_plans_per_cycle
max_repair_attempts
max_files_changed
max_diff_size
max_new_dependencies
max_validation_time
max_validation_cost
max_self_improvement_cost
cooldown_after_failed_repair
```

自律改善自体が無制限Resource consumerにならないこと。

---

# 7. 自律改善専用Failure Modes

```text
false positive Finding
wrong diagnosis
wrong repair
repair causes regression
tests insufficient
tests weakened intentionally
branch conflict
dependency compromise
repair loop
oscillating repair
same rejected patch regenerated
provider gives unstable code
improvement exceeds budget
recovery itself broken
production differs from test environment
successful benchmark but real KPI worsens
```

各Failure Modeには停止/rollback/escalation条件を用意する。

---

# 8. 現行開発との位置関係

```text
Phase 3.5 Kernel Hardening
├ State/Resume
├ Approval
├ External Effect
├ Migration
├ Recovery
├ Provider Contract
└ Gate System

          ↓ prerequisites complete

F0 Self-Observation
F1 Self-Diagnosis
F2 Improvement Planning

          ↓ Recovery/rollback完全成立

F3 Repair Candidate
F4 Validation
F5 Controlled Self-Repair

          ↓ Tool isolation/security成立

F6 Self-Extension

          ↓ sufficient successful traces

F7 Workflow Promotion

          ↓

F8 Continuous Improvement
```

---

# 9. 現時点で開始可能な範囲

現在から並行設計・実装してよい:

```text
F0 Self-Observation
F1 Self-Diagnosis
F2 Improvement Planning
```

ただしread-only。

禁止:

```text
current code modification
automatic branch creation
automatic merge
Tool activation
Workflow promotion
```

---

# 10. F3以降の進入条件

```text
Phase 3.5 Gate complete
Recovery restore works
last-known-good exists
rollback works
repair branch works
exact HEAD CI green
Protected Tests established
Event/Audit security established
Tool execution containment established
```

一つでも未達ならF3進行禁止。

---

# 11. Self-Repair開始条件

```text
ImprovementFinding contract verified
ImprovementPlan contract verified
deterministic validation pipeline verified
risk classification verified
repair budget verified
repair attempt ceiling verified
```

---

# 12. Self-Extension開始条件

```text
generated code isolation
dependency policy
network isolation
filesystem isolation
CPU/RAM/process limits
Tool security validation
candidate registry
disable/revoke
```

---

# 13. Workflow Promotion開始条件

```text
successful trace persistence
trace normalization
pattern comparison
representative dataset
benchmark framework
workflow versioning
workflow rollback
```

---

# 14. 最終成功指標

```text
Failure recurrence ↓
Mean time to diagnose ↓
Mean time to recover ↓
Human repair intervention ↓
Repeated LLM work ↓
Cost per successful task ↓
Latency ↓
Workflow reuse ↑
Provider survivability ↑
Regression escape rate ↓
Rollback success rate ↑
Invariant violation = 0
Unauthorized modification = 0
Hard Budget violation = 0
```

---

# 15. 最終Definition

自己改善可能:

```text
自分の異常・不足を観測できる
+
根拠付きで原因候補を形成できる
+
検証可能な改善計画を作れる
+
隔離環境で変更候補を生成できる
+
自分とは独立した基準で変更を検証できる
+
悪化時に拒否・rollbackできる
+
許可範囲を越えて自分を書換えられない
```

自己進化可能:

```text
上記
+
新しい能力を安全に追加できる
+
繰り返される知能処理をWorkflowへ昇格できる
+
改善Loopそのものを継続運用できる
```

---

# 16. 最終優先順位

```text
1 Observe correctly
2 Diagnose correctly
3 Plan safely
4 Recover reliably
5 Modify reversibly
6 Validate independently
7 Repair autonomously
8 Extend safely
9 Compile intelligence into workflows
10 Optimize continuously
```

> 自律性は先に増やさない。  
> 観測可能性・停止可能性・復旧可能性・検証可能性が増えた分だけ、自律権限を一段ずつ解禁する。
