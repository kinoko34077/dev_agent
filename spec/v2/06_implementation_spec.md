# 実装仕様（現行 Phase 7）

この文書は現行 v2 の実装契約と依存方向を定義する。配置を素早く探すための索引は `docs/SYSTEM_MAP.md`、運用手順は `docs/CODEX_COMMANDER.md`、現在の実装証跡は `docs/CURRENT_STATE.md` と分担する。alpha0/Phase 0 の候補配置を現行仕様として扱わない。

## 実装境界

| 領域 | 実装場所 | 主責務 |
| --- | --- | --- |
| Domain | `src/dev_agent/domain/` | Task、Model、Tool、Event の typed protocol |
| Security / Policy | `src/dev_agent/security/`、`src/dev_agent/intelligence/` | scope、audit、Evaluator、finite escalation、authority policy |
| State | `src/dev_agent/state/` | SQLite connection/transaction owner、core/effect repository |
| Tools | `src/dev_agent/tools/` | Tool policy、executor、effect guard、timeout/cancel |
| Resources | `src/dev_agent/resources/` | ResourceLedger facade、catalog/observation/quota/health/budget、router/control |
| Providers | `src/dev_agent/providers/` | Adapter、Factory、Registry、Dispatcher、journal |
| AgentBackend contract | `src/dev_agent/backends/` | 外部Agent harnessとのthin typed contract。実adapterは別slice |
| Runtime | `src/dev_agent/runtime/` | Controller、model turn、legacy compatibility、checkpoint/resume |
| Scheduler | `src/dev_agent/scheduler/` | DurableQueue、WorkerRunner、lease、quota wake/requalification |
| Operation | `src/dev_agent/operation.py`、`src/dev_agent/__main__.py` | 人間向け start/submit/status/stop と maintenance composition |
| Recovery | `recovery/` | Runtime から独立した backup/restore/diagnostics/repair boundary |
| DevFarm | `scripts/devfarm*.py`、`.devfarm/` | development-only proposal、verification、Commander parent plan |
| Formal contract | `spec/v2/` | API/implementation contract、requirements、ADR、Gate、traceability |

`.devfarm/` は runtime の正式データ領域ではなく、development-only の ignored artifact である。Credentials、`.env*`、private key、budget authority、Recovery、Gate status は保護領域として扱う。

## 許可された依存方向

下位の実装は上位の責務を直接飛び越えず、次の一方向を基本とする。

```text
domain
  ↑
policy / security
  ↑
state / tools / resources / providers / intelligence
  ↑
runtime
  ↑
scheduler / operation composition
```

- `domain` は runtime、provider SDK、SQLite 実装を import しない。
- `policy/security` は domain を利用できるが、runtime の具体実装を所有しない。
- `state`、`tools`、`resources`、`providers`、`intelligence` は domain と policy の typed 契約を利用できるが、互いの内部実体を直接参照しない。
- `runtime` は上記 facade/public protocol を composition する。Provider 通信は `ProviderDispatcher`、Resource 操作は `ResourceControlPlane` を経由する。
- `scheduler` は Queue/Worker/lease と runtime lifecycle を接続するが、Provider SDK の分岐や新しい Task state machine を所有しない。
- `operation` は既存部品を composition する薄い入口であり、production scheduler を並立させない。
- `backends` は外部Agent harnessのidentity、session、event、cancellation、resultをtyped化する薄い境界であり、Runtime/State/Scheduler/Budget/Recoveryの所有権を持たない。Backend固有adapterはこの境界の外側に置く。
- `recovery/` は runtime/controller から独立し、durable artifact と operator authority を扱う。Recovery が Controller の内部状態を書き換える設計にしない。
- `devfarm` は production scheduler/state/authority と独立した development-only 層で、既存 WorkerRunner/ProviderFactory 等の公開境界を composition できるが、公式 branch を自動変更しない。

## 禁止される実装

- v1 の `Executor`、`LLMClient`、`RecursionManager` を v2 runtime から import しない。
- Provider Adapter から runtime、Controller、Gate、budget authority を import しない。
- Resource Store、State Repository、Tool Executor から Controller を参照しない。
- Router から Dispatcher を参照せず、Dispatcher から Router の内部 ledger を直接操作しない。
- Recovery と DevFarm に production scheduler、独立 durable state machine、独自の authority を追加しない。
- OSS Agent framework、MCP、A2A、UI を導入して既存の state/queue/authority を二重化しない。
- 任意 Python import、無制限 shell、infinite retry、blind quota retry、未知の外部効果の自動 replay を安全機構として扱わない。

## 状態・証跡契約

- schema migration は既存の ordered migration owner を通し、不要な schema version を追加しない。
- critical transition は StateStore の transaction owner と `commit_transition` を通す。
- 外部 dispatch は intent、budget reservation、provider audit、lease/fencing を結び、timeout/decode/ownership不明は UNKNOWN/reconciliation として保存する。
- FiniteLifecycle の使用数は durable Task/Event history から復元し、process restart で retry 上限をリセットしない。
- Quota は observation の `quota_domain` と reset/blocked_until を正本とし、reset 到達だけで復帰させず、bounded probe と正常観測の永続化後に routing へ戻す。
- Evidence-based routing は現段階では advisory とし、minimum sample、freshness、rollback 条件を満たすまで hard routing policy に接続しない。

## 実装・検証ルール

変更は focused tests、`tests/v2` full regression、関連する read-only Gate check の順で検証する。Provider の live qualification は CI へ混ぜず、operator 実行と compact な evidence artifact で管理する。Gate を evidence artifact だけで昇格させず、Current State と Traceability の所有文書を同じ変更群で同期する。
