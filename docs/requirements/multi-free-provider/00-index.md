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

## 現行のPhase 6A実装境界

Phase 6Aの第一バッチとして、ResourceLedger schema v6のquota_domain identity、quota_observations、operational observation fields、fresh quota headroom routing、provider-neutralなquota observation取り込みを実装した。同一quota domainの複数Credentialは残量を加算せず、fresh observationの保守的headroomとconcurrency hard filterを使う。Phase 7A/Bの移行境界として、Taskへtyped profileを追加し、決定的なIntelligence Policyの範囲をModelRequest metadataへ渡す。Groq、Cloudflare Workers AI、Mistral、OpenRouter Freeはnormalized Adapter/contract境界まで追加し、Groq／Cloudflareにはopt-in HTTP Adapterもある。Cloudflare Workers AIはcanonical live qualificationを取得済み、GroqはHTTP 403、Mistral/OpenRouterはlive HTTP未実証である。

- G6O1は BLOCKED_EXTERNAL のまま維持する。
- 通常Provider経路は Controller -> ProviderDispatcher -> ProviderRegistry -> Concrete Provider とする。
- Controllerのdirect Provider処理は compatibility / legacy path として残す。
- ExecutionContext、ToolRuntime.bound_to()、RuntimeState、AuditRecorderは既存境界を維持する。
- Model tierによる実Provider選択、Hedge、AgentBackend、MCP/API、Phase 7C以降、未実装Providerの固有header解析は次段階へ送る。Groqのrate-limit headerを正規化した `usage.quota_observation` の取り込みとPhase 7A/BのTask profile policyは現行境界に含む。Cloudflare live qualificationはToolCall往復とdurable auditを確認したが、quota情報未報告のためquota observationはunknownである。

## 選択的ロードの目安

- Provider追加: 03, 04
- quota-aware routing: 03
- Intelligence hierarchy: 02, 06
- Codex/MCP: 05
- Phase移行判断: 07

## 要件と現行実装の境界

本章群は設計入力と実装進捗の境界を兼ねる。ここに書かれた将来要件を現行Gateの達成として扱わない。現行Phase 6では、FoundationがVERIFIED、G6O2〜G6O6がVERIFIED、G6O1が外部条件待ちである。Phase 6A quota基盤とAdapter contractの成功は、実Provider live qualificationとは別の証跡である。
