# AgentBackend、Codex、MCP、authority requirements

対象: 添付要件定義書 §20–26

## 20. ModelProviderとAgentBackendの分離

ModelProviderはモデル推論、tool call、usage、Providerエラーを提供する。一方 AgentBackend は、より上位の実行単位として次を扱う。

- task loop
- context
- tool policy
- approval
- checkpoint
- cancellation
- recovery

この二つを一つのProvider adapterへ混ぜない。現行Phase 6では ModelProvider とController/Task実行境界を維持し、AgentBackend は将来要件として予約する。

## 21. AgentBackendRouter

将来のBackend選択は、モデル名だけでなく次の軸を含む。

- intelligence tier
- task class
- autonomy
- tool access
- approval requirement
- privacy
- cost/quota
- durability/recovery capability

Routerの複製を作らず、Provider選択とBackend選択の責務境界を定義してから実装する。

## 22. Codex

Codexを将来のAgentBackend候補とする場合、次を別個に検証する。

- backend capability
- session/context lifecycle
- tool and approval semantics
- usage/cost reporting
- cancellation and recovery
- durable audit

Codex接続をProvider追加と同一視しない。実接続、認証、運用権限、外部証跡が揃うまで、GateをVERIFIEDへ上げない。

### CODEX-001: backend boundary

Codex固有のsession/API仕様はAgentBackend adapter内に閉じ、Controller、ResourceRouter、Budgetへ漏らさない。

### CODEX-002: controlled execution

外部AgentBackendのdispatchは、既存のExecutionContext、approval、lease/fencing、durable intent、reconciliationを経由する。

## 23. API/MCP

API/MCPは将来の外部Agent integration境界である。

### MCP-001: tool contract

MCP toolはschema、引数、結果、byte limit、timeout、error、approval、redactionを明示する。

### MCP-002: trust boundary

MCP server/clientはuntrusted入力を扱う可能性があるため、process isolation、credential分離、resource policy、監査を省略しない。

### MCP-003: durable operation

外部MCP operationは、送信前後のintent、unknown、retry、reconciliationを durable に記録する。

この移行前調整ではAPI/MCPを追加しない。

## 24. Human authority

人間だけが変更できる操作を明示的に分離する。

- protected hard budget policyの変更
- Recovery Reserveの使用承認
- provider credential/configuration
- privacy policyの緩和
- rollback/repair
- Phase/Gate promotion

通常Runtimeがこれらを自己承認しない。Python上の命名だけでなく、将来はOS、deployment、secret store、運用手順でも保護する。

## 25. Budget authority

通常Runtimeの BudgetGovernor は永続化されたpolicyをread-onlyで読む。Hard Cap変更は通常Taskから呼べないprotected admin pathに分離する。

G6O1は、実paid Providerのworst-case qualificationとdeployment-owned protected budget configurationが揃うまで BLOCKED_EXTERNAL のまま維持する。

## 26. Recovery Reserve authority

recovery=True のようなフラグだけで通常AgentがRecovery Reserveを使用できる設計にしない。

- Recovery task classまたは同等の明示的権限を定義する。
- reserve使用者、理由、上限、承認、結果をdurable auditへ記録する。
- 通常処理のbudget経路からRecovery Reserveを不可視にしない。

Recovery ReserveのRecoveryTaskAuthority境界は現行Phase 6へ実装済みである。AgentBackend／MCPからの利用や、より広いdeployment／OS-level authorityは次要件として扱う。

## Development Commanderとの境界

`scripts/devfarm_commander.py` のCommander親Planは、Codexが自分のTaskと
development-only Worker Taskを分解・追跡・reviewするための補助層である。既存の
DevFarm manifest、Remote proposal、Host Verificationをcompositionするだけで、
Production RuntimeのTask state、Scheduler、Budget、Quota、Human Authorityを
所有しない。これは `AgentBackend` や正式なMulti-Agent runtimeの実装ではなく、
将来MCPで公開する場合も、このPlanと既存Control Planeの境界を包むだけとする。
