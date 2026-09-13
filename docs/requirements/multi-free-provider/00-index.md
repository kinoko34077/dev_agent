# Multi-Free Provider Requirements Index

> Documentation role: stable requirements and acceptance only. Implementation
> progress and current Provider evidence belong to `docs/CURRENT_STATE.md` and
> `spec/v2/evidence/**`; this index is not a current-state authority.

Status: DRAFT -> ACCEPTED候補

対象は dev_agent v2。既存Phase 0〜6の仕様・Invariantを維持し、本要件と衝突する旧計画だけを本要件で更新する。

## 章一覧

| 章 | 範囲 | 主な用途 |
| --- | --- | --- |
| [01 Purpose and Operating Model](01-purpose-and-operating-model.md) | 1-2 | 目的、継続稼働、判断と実行の分離 |
| [02 Architecture, Intelligence, and Task](02-architecture-intelligence-and-task.md) | 3-6 | Control Plane、L0〜L3、Role/Model、Task分類 |
| [03 Resource, Quota, Capability, and Routing](03-resource-quota-capability-and-routing.md) | 7-14 | Resource Pool、quota、Observation、Capability、Router、Cost、Escalation |
| [04 Provider Policy, Privacy, and Hedging](04-provider-policy-privacy-and-hedging.md) | 15-19 | Provider追加、Adapter、Privacy、Hedge、Meta Provider |
| [05 Agent Backend, Codex, MCP, and Authority](05-agent-backend-codex-mcp-and-authority.md) | 20-26 | AgentBackend、Codex、MCP、Human/Budget/Recovery authority |
| [06 Workflow, Evaluator, Audit, Metrics, and Survival](06-workflow-evaluator-audit-metrics-and-survival.md) | 27-33 | Workflow promotion、Evaluator、Audit、Metric、Survival、障害耐性 |
| [07 Phase Roadmap, Invariants, and Target State](07-phase-roadmap-invariants-and-target-state.md) | 34-42 | Phase 6更新、Phase 7条件、ロードマップ、完成像 |

## 文書の役割と更新境界

この章群は、multi-provider運用の安定要求、不変条件、authority、acceptanceを定義する。個別の実装進捗、現在のProvider availability、テスト件数、HEAD、未完Taskはここへ追加しない。

- 実装の現在状態: [`docs/CURRENT_STATE.md`](../../CURRENT_STATE.md)
- 大きな順序: [`docs/V2_EXECUTION_PLAN.md`](../../V2_EXECUTION_PLAN.md)
- 詳細Gate: [`docs/V2_DETAILED_ROADMAP.md`](../../V2_DETAILED_ROADMAP.md)
- 観測済みProvider/model証拠: [`spec/v2/evidence/`](../../../spec/v2/evidence/)
- 意思決定理由: [`spec/v2/adr/`](../../../spec/v2/adr/)

章を更新する場合も、要求とacceptanceが変わった時だけ該当章を変更し、現在状態や作業履歴は上記の正本へ記録する。要求を満たしたことは、対応する実装・テスト・Evidenceを`spec/v2/TRACEABILITY.md`で追跡し、無関係なGateへ自動的に波及させない。
