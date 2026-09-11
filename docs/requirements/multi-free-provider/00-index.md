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

## 現行のPhase 6A / Phase 7実装境界

Phase 6Aの第一バッチとして、ResourceLedger schema v8のquota_domain identity、generic quota observation、operational observation fields、fresh quota headroom routing、provider-neutralなquota observation取り込み、metric／window／reset／blocked stateを実装した。同一quota domainの複数Credentialは残量を加算せず、fresh observationの保守的headroomとconcurrency hard filterを使う。Phase 7A/BではTaskへtyped profileを追加し、決定的なIntelligence Policyの範囲、minimum tier、thinking effortをModelRequest metadataへ渡す。明示opt-in時はresource metadataのintelligence tierとexact matchしてroutingを制約するが、通常routingは変更しない。Phase 7C/D/Eではhost evaluator、durable evidence、有限plan、明示review、accepted handoffのcanonical実dispatch、冪等なTask lifecycle、bounded workflow proposal、Remote proposal／Host verification分離を実装した。Gemini 3.5 Flash-Liteと3.8 Flash、Cloudflare Workers AI、OpenRouter Free、Ollamaは対応するlive qualificationを取得済みである。Groqはmodels endpoint HTTP 403、SambaNovaはmodels endpoint HTTP 200後の推論HTTP 429/402、Mistralは推論HTTP 429で未 qualificationである。

- G6O1は BLOCKED_EXTERNAL のまま維持する。
- 通常Provider経路は Controller -> ProviderDispatcher -> ProviderRegistry -> Concrete Provider とする。
- Controllerのdirect Provider処理は compatibility / legacy path として残す。
- ExecutionContext、ToolRuntime.bound_to()、RuntimeState、AuditRecorderは既存境界を維持する。
- 後段の実績ベースrouting、Hedge、AgentBackend実adapter、MCP/API、Task lifecycleの次cycle自動循環、Provider別の完全なreset-aware Schedulerは次段階へ送る。明示opt-inのresource tier routing、GroqとSambaNovaのrate-limit headerを正規化した `usage.quota_observation` の取り込み、CloudflareのNeuron消費推定（`authority=estimated`）、quota block／保守的cooldown policy、Phase 7A/BのTask profile policyは現行境界に含む。Phase 7Dではhost/operatorがplanを明示reviewし、accepted/rejectedと受理済みplanのdispatch-ready handoffをdurable eventへ記録する。accepted handoffは`EscalationExecutor`が既存のeffect intent／budget／reconciliationを再利用してcanonical ProviderDispatcherへ有限dispatchし、`TaskLifecycleCoordinator`がhost outcomeを冪等なtransitionへ適用する。Phase 7後段のthin AgentBackend contract、typed `BackendAdmission`付きdispatcher、ModelProviderとAgentBackendを分けるexecution target seamも実装済みだが、実Codex adapterは未実装である。CloudflareとOpenRouter Freeのlive qualificationはToolCall往復とdurable auditを確認したが、quota残量は未報告のためunknownである。SambaNovaは `/v1/models` 接続を確認したが、推論はHTTP 429/402で停止しているためfree-provider qualificationから除外し、成功・無償tier・paid worst-caseを推測しない。Mistralはキー読込み後の推論HTTP 429で未資格化であり、成功や無料枠を推測しない。
- 追加の接続境界として、`GEMINI_API_KEY_2`〜`_5`はproject-scoped quota domainを持つ個別Gemini bindingへ、Ollama Cloudは`ollama_cloud`、Vercel AI Gatewayは`vercel`へ分離して構成できる。両者は既存OpenAI-compatible HTTP adapterを利用するが、API keyの存在だけではqualification／billing admissionにならない。`TrustedResourceProfile.billing_mode`は`free_fixed`と`recurring_allowance`／`recurring_credit`を区別し、exact model profileがない新bindingはproduction routingへ投影しない。

## 選択的ロードの目安

- Provider追加: 03, 04
- quota-aware routing: 03
- Intelligence hierarchy: 02, 06
- Codex/MCP: 05
- Phase移行判断: 07

## 要件と現行実装の境界

本章群は設計入力と実装進捗の境界を兼ねる。ここに書かれた将来要件を現行Gateの達成として扱わない。現行Phase 6では、FoundationがVERIFIED、G6O2〜G6O6がVERIFIED、G6O1が外部条件待ちである。Phase 6A quota基盤とAdapter contract、Phase 7のlocal/live qualification、DevFarm host verificationはそれぞれ別の証跡であり、成功を無関係なGateへ波及させない。
