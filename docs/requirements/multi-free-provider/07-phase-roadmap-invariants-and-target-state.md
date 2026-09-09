# Phase roadmap、invariants、target state

対象: 添付要件定義書 §34–42

## 34. Phase 6の位置付け

現行Phase 6は基盤・運用hardeningの完了段階であり、「失敗している状態」と扱わない。

- Phase 6 foundation = VERIFIED
- G6O2〜G6O6 = VERIFIED
- G6O1 = BLOCKED_EXTERNAL

G6O1の未達理由は、コード不足の推測ではなく、real paid Providerによるworst-case qualificationとdeployment-owned protected budget configurationという外部条件である。fake evidenceで昇格しない。

## 35. Multi-Free移行後のPhase 6 acceptance

次段階のMulti-Free要件でPhase 6を再評価する場合、少なくとも次を別々に証明する。

- free-first policy
- quota-aware reservation/observation
- Provider capability routing
- privacy policy
- fallback and outage tolerance
- durable audit
- actual provider E2E
- recovery and rollback

これらを今回の移行前調整で実装したことにはしない。

## 36. Phase 7 entry conditions

Phase 7へ進む前に、Phase 6全体のAND条件を満たす。個別GateのVERIFIEDを、無関係なlive ProviderやRecovery条件で混ぜない。

Phase 7の候補条件:

- Phase 6 operational acceptance
- protected authority boundary
- provider/resource evidence
- recovery drill
- exact-head CI/evidence
- current-state documentation

## 37. Phase 7 additions

Phase 7候補には、AgentBackend、Codex/MCP integration、workflow promotion、self-improvement、multi-agent orchestrationなどを含む。ただし本章では計画対象として記録するだけで、今回実装しない。

## 38. External agent timing

外部Agent、Codex、MCP/APIは、ModelProviderの追加とは別の段階で導入する。先に実行、権限、監査、recovery、cost/quotaの境界を確定する。

## 39. Non-goals

今回の移行前調整では、次を行わない。

- quota_domain
- quota observation schema
- Groq、Cloudflare、Mistral、OpenRouterなどの新Adapter
- Router scoring変更
- intelligence tier
- Hedged Request
- AgentBackend、MCP/API
- Phase 7、Self-Improvement
- 大規模Controller rewrite
- Resource schema拡張

## 40. Invariants

次を移行中も維持する。

### INV-016: canonical provider path

通常のProvider実行は Controller -> ProviderDispatcher -> ProviderRegistry -> Concrete Provider を正本とする。

### INV-017: compatibility direct path

Controller内のdirect Provider pathは、既存互換のためだけに残し、新規Provider機能の正本にしない。

### INV-018: durable external uncertainty

外部dispatch後のtimeout、decode/validation failure、ownership不明はunknown/reconciliationとして扱う。

### INV-019: protected budget

通常RuntimeはHard Capを自己変更できない。G6O1の外部条件を偽装しない。

### INV-020: no shared mutable run context

Worker/run固有のExecutionContext、ToolRuntime binding、lease proofを共有mutable Controller状態へ戻さない。

## 41. Implementation order

要件移行後の推奨順序は次のとおり。

1. ProviderRegistry/Dispatcherの既存境界を確認する。
2. Provider contractとcapabilityを狭く固定する。
3. Resource/quota observationを設計し、migrationを準備する。
4. free-first、privacy、fallbackを実装する。
5. live Provider E2Eとdurable evidenceを取得する。
6. AgentBackend/Codex/MCPを別境界で追加する。
7. evaluator、workflow promotion、multi-agentを段階導入する。

この順序は、今回の「移行前軽量調整」後に新要件を投入する際の参照用である。

## 42. Target state

将来のtarget flowは次のように整理する。

Controller -> ProviderDispatcher -> ProviderRegistry -> ResourceControlPlane/Router -> Budget/Quota -> Concrete Provider/AgentBackend -> durable audit/reconciliation

ControllerへProvider固有分岐、quota判断、fallback、外部Agent固有処理を再追加しない。実装済み境界と将来境界を混同せず、各段階のevidenceを独立して管理する。

