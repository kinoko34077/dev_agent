# Multi-Free Provider Requirements Index

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

## 現行の移行前調整

この要件を投入する前の調整では、Provider追加やquota schema変更を行わない。

- G6O1は BLOCKED_EXTERNAL のまま維持する。
- 通常Provider経路は Controller -> ProviderDispatcher -> ProviderRegistry -> Concrete Provider とする。
- Controllerのdirect Provider処理は compatibility / legacy path として残す。
- ExecutionContext、ToolRuntime.bound_to()、RuntimeState、AuditRecorderは既存境界を維持する。
- Resource Schema、Provider追加、quota_domain、Intelligence Tier、Hedge、AgentBackend、MCP/API、Phase 7は次段階へ送る。

## 選択的ロードの目安

- Provider追加: 03, 04
- quota-aware routing: 03
- Intelligence hierarchy: 02, 06
- Codex/MCP: 05
- Phase移行判断: 07

## 要件と現行実装の境界

本章群は次段階の設計入力であり、ここに書かれた将来要件を現行Gateの達成として扱わない。現行Phase 6では、FoundationがVERIFIED、G6O2〜G6O6がVERIFIED、G6O1が外部条件待ちである。
