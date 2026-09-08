# dev_agent v2 現行ロードマップ 簡易版

Status: CURRENT ROADMAP  
Current Phase: **Phase 6 — Resource / Survival / Recovery (6A〜6E)**

---

## Phase 0 — Baseline / 仕様基盤

### 目的
v1を保全し、v2を独立して再構築できる開発基盤を作る。

### 主な内容
- v1 baseline固定
- `legacy/v1-final`
- `v2/bootstrap`
- v2仕様書・Invariant・ADR
- v1資産の棚卸し
- v2 / v1依存分離

### 状態
**概ね完了**

---

## Phase 1 — Protocol / Recovery基礎

### 目的
Providerに依存しない内部Protocolと、通常Runtimeとは独立したRecovery入口を作る。

### 主な内容
- Task / Step
- ModelRequest / ModelResponse
- ToolCall / ToolResult
- Event
- serialization
- Recovery diagnostics skeleton

### 状態
**基礎完了**
Recoveryは後段で継続強化。

---

## Phase 2 — 最小決定的Kernel

### 目的
LLMを自由に暴走させず、Controllerが有限・明示的に実行を管理する最小Kernelを完成させる。

### 主な内容
- FakeProvider
- iterative Controller
- Tool Registry
- checkpoint
- Event
- 実行回数上限
- Model → Tool → ToolResult → Final の完走

### 状態
**alpha基礎完了**

---

# Phase 3 — Task / Policy / Durable State

### 目的
Task実行を再起動・権限・副作用へ耐えられる構造にする。

### 主な内容
- Task Graph
- SQLite State
- resume
- Path Policy
- Approval
- Idempotency
- Side-effect管理

### 状態
**primitive / integration verified**
Phase 3.5のcurrent acceptanceは完了。

---

# Phase 3.5 — Kernel Integration Hardening

## 現在地

### 目的
KernelのTrust Boundaryを閉じ、後段機能を安全に載せられる状態にする。

### 主な対象
- crash / resume
- transaction
- migration
- cancellation
- Tool timeout / isolation
- exact-call approval
- external effect state
- reconciliation
- Tool schema
- Provider error分類
- Audit / sensitive data
- Recovery validation
- Gate管理

### 完了後
**Phase 4 / 5へ進行済み**

### 状態
**完了（current acceptance verified）**

---

# Phase 4 — Local Provider

### 目的
クラウドへ依存せず、実モデルでKernelを完走させる。

### 主な内容
- Local Provider Adapter
- Ollama等
- Provider Contract Harness
- 実モデルによるText出力
- 実Tool Call
- ToolResult → final response
- model適格性評価

### 完了条件
代表タスクをLocal Providerで安全に完走できる。現状はOllama qwen3:8bのController E2Eで検証済み。

---

# Phase 5 — Multi-Provider / Cloud Provider

### 目的
Providerを交換・追加してもCoreが変わらない状態を証明する。

### 主な内容
- Gemini
- OpenAI-compatible等
- live Contract Probe
- Provider error taxonomy
- capability matrix
- Provider差のnormalize

### 完了条件
複数の独立Providerが同一Kernel contractを満たす。現状はGemini live contractで検証済み。

---

# Phase 6 — Resource / Survival / Recovery

### 目的
有限資源・Provider障害・Runtime故障に耐えて継続運転できるようにする。

### 主な内容

#### Resource
- Resource Ledger
- quota / availability
- cost tracking

#### Budget
- Hard Budget
- Recovery Reserve
- paid call制御

#### Router
- Provider選択
- fallback
- capability routing

#### Survival
- NORMAL
- CONSERVE
- SURVIVAL

#### Recovery
- Recovery CLI
- backup / restore
- last-known-good
- rollback
- repair branch
- external rescue

### 完了後
v2の通常運転基盤が一段落。live rollback / repair drill、automatic retry policy、generated Tool lifecycleは後段要件。

### 6A〜6Eの現在状態

- 6A Resource Ledger / Budget Governor: VERIFIED
- 6B Router / Survival Modes: VERIFIED
- 6C Recovery operationalization: VERIFIED
- 6D Scheduler / Worker ownership: VERIFIED
- 6E Kernel integration / Gate evidence / documentation: VERIFIED

詳細は `docs/PHASE6_PLAN.md` と `spec/v2/GATE_STATUS.json` Stage Fを参照。

---

# Phase 7 — Safe Extension / Self-Improvement

### 目的
dev_agent自身が能力改善・拡張を安全に行える基盤を構築する。

### 主な方向
- evaluator
- critic
- improvement observation
- diagnosis
- repair candidate
- validation
- controlled self-repair
- Tool / Skill generation
- Workflow library
- Agent処理のWorkflow化

### 備考
詳細は別紙の
**「自律改善・自己進化システム 目標段階定義」**
で管理する。

---

# Phase 8 — Multi-Agent / AI Company

### 目的
複数Role・複数Agentによる実務運用を成立させる。

### 主な内容
- Role Manifest
- bounded handoff
- Multi-Agent
- task ownership
- AI Company benchmark
- Revenue Ledger
- 利益再投資
- Human Approval境界

### 完了条件
Provider障害下でも実タスクを安全に遂行できる。

---

# Phase 9 — Virtual Office UI

### 目的
完成したRuntimeを人間が監視・承認・操作しやすいUIへ載せる。

### 主な内容
- Task状態
- Agent / Role状態
- Approval
- Resource
- Audit
- Recovery
- 運用可視化

### 原則
UIを正本にしない。

```text
Runtime / Durable State
        ↓
      API
        ↓
       UI
```

---

# 全体像

```text
Phase 0
Baseline / Spec
      ↓
Phase 1
Protocol / Recovery Foundation
      ↓
Phase 2
Deterministic Kernel
      ↓
Phase 3
Task / Policy / Durable State
      ↓
Phase 6      ← CURRENT
Kernel Trust Boundary Hardening
      ↓
Phase 4
Local Provider
      ↓
Phase 5
Multi / Cloud Provider
      ↓
Phase 6
Resource / Budget / Survival / Recovery
      ↓
──────── 通常基盤 一段落 ────────
      ↓
Phase 7
Self-Improvement / Safe Extension
      ↓
Phase 8
Multi-Agent / AI Company
      ↓
Phase 9
Virtual Office UI
```

---

# 現在の優先順位

```text
1. Phase 3.5〜5のcurrent acceptanceを維持
2. Resource / Budget / Survival / Recoveryを運用化
3. live recovery drillと後段要件を検証
5. 通常基盤を一度固定
6. 自律改善系を本格導入
7. Multi-Agent / AI Companyへ拡張
8. 最後にUI
```

---

# 基本原則

```text
Safety
↓
Durability
↓
Recoverability
↓
Resource Control
↓
Autonomy
↓
Business Expansion
↓
UI
```

上位機能の都合で下位Gateを飛ばさない。

現在はまず、

> **Phase 6までで「自律改善を載せても壊れにくい通常基盤」を完成させる**

ことを優先する。
