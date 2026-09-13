# dev_agent v2 実行計画

この文書は現在の大きな順序だけを示すMain Roadmapである。詳細なGate、依存関係、完了条件は [`V2_DETAILED_ROADMAP.md`](V2_DETAILED_ROADMAP.md) を正本とする。要求は`docs/requirements/**`、decision rationaleは`spec/v2/adr/**`、観測証拠は`spec/v2/evidence/**`、Gate statusは`spec/v2/GATE_STATUS.json`を参照する。

## Current phase

Phase 7後半の安全な拡張と開発運用移管。既存のKernel、Resource/Provider、Task/Scheduler、Recovery、DevFarm、Supervisor、Host Verification、Review/Integration境界は維持する。G6O1は未検証の外部Gateだが、Human決定により現行roadmapでは非ブロッキング凍結中である。

## Implementation frontier

- Group Dのsession identity、bounded artifact reference、reconciliation replay、明示的Backend discovery authority。
- Free L1 WorkerのSupervisor/Host Verification/ReviewDecision/REWORK/依存integration経路。
- Free L2 Plannerのproposal-only adapter、strict JSON boundary、RootPlanningProposal、Host-only DevelopmentPlanningBridge、および明示的なModel Catalog / Benchmark Catalog / Capability Catalog / Runtime admission境界。
- MCPはschema-only contractまで。runtime adapter、Compression、OpenAI/Claude APIは未接続。

## Current Gate

### D0 — Documentation SSOT consolidation

Current State、Main/Detailed Roadmap、Requirements、ADR、Evidence、作業Planの役割を分離し、過去の完了Planをarchiveした。現在の詳細正本は`V2_DETAILED_ROADMAP.md`である。

### D1/D2 — Planner-to-Worker live development slice

次の最重要Gateは、明示opt-inされたqualified L2 poolから実Free L2 Plannerのvalid proposalをHost validationへ通し、既存BridgeからCommander Plan、qualified Free L1 Worker、Host Verification、Codex review-only、Host deterministic integrationまで一続きで証明すること。Model discovery / benchmark / capability evidenceは候補化の入力に留まり、qualification・billing・privacy・quota・healthを代替しない。confirmed failover-safe failureは同一tierの別bindingへ切替え、UNKNOWNはreconciliationへ閉じる。現在は、複数exact L2 poolの構成とbounded failoverまでは実装済みだが、live valid proposalは未検証である。

## Operational acceptance

Code、local regression、Host Verification、Git integration、remote push、exact-head CIは別Evidenceとして扱う。Provider request到達、adapter存在、schema-only MCP、モデル自己申告だけではGateを閉じない。詳細順序はD3 Worker reliability、D4 concrete session restart/discovery、D5 MCP thin runtime、D6 Reviewer Shadow、D7 LOW/NORMAL Codex-less cycle、D8–D9 Self-Improvementへ続く。

## External / frozen

G6O1-SIM/LIVE、real paid-provider qualification、Compression Service、OpenAI API、Claude API、OS-level sandbox evidence、Production auto-deploy、unbounded autonomous loop、Discord、Virtual Office UIはHumanの明示再開まで着手しない。G6O1は`DEFERRED_FROZEN` / `NOT VERIFIED` / `roadmap_blocking=false`として扱い、原要求を削除・昇格しない。

## Development rules

Free Workerに適した狭いTaskはWorker-first。Codexはdecomposition、authority-sensitive判断、review、integration、exception handlingを担当し、直接実装する場合は具体的理由をEvidenceへ残す。新しいScheduler、state machine、retry framework、Agent framework、MCP独自実行系は追加しない。

## References

- 現在状態: [`CURRENT_STATE.md`](CURRENT_STATE.md)
- 詳細順序: [`V2_DETAILED_ROADMAP.md`](V2_DETAILED_ROADMAP.md)
- 文書分類: [`DOCUMENTATION_INVENTORY.md`](DOCUMENTATION_INVENTORY.md)
- 運用契約: [`CODEX_COMMANDER.md`](CODEX_COMMANDER.md) / [`CODEX_SUPERVISOR.md`](CODEX_SUPERVISOR.md)
- 検証証拠: [`spec/v2/evidence/`](../spec/v2/evidence/)
