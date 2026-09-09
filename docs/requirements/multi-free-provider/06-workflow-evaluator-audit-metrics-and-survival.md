# Workflow、評価、監査、metrics、survival requirements

対象: 添付要件定義書 §27–33

## 27. Workflow promotion

Workflowは自由形式の自動昇格ではなく、検証済みmanifestと人間承認を前提にする。

### WF-001: promotion criteria

昇格には少なくとも次を含める。

- test/evaluation evidence
- security/privacy review
- budget/quota policy
- rollback path
- operator ownership

### WF-002: bounded autonomy

自動化されたWorkflowは、許可されたtask class、tool、Provider、予算、retry、時間の範囲を越えない。

## 28. Evaluator

Evaluatorは実行系から独立した判定層とし、成功率だけでなく次を評価する。

- useful task completion
- correctness
- policy compliance
- cost/quota consumption
- latency
- recovery behavior
- external side effects

### EVAL-001: evidence-based promotion

Evaluatorの結果はpromotionの証跡として保存する。単一の成功例や自己申告だけでGateを上げない。

### EVAL-002: adversarial cases

timeout、unknown、stale worker、duplicate dispatch、quota exhaustion、provider outage、privacy violationを評価対象に含める。

## 29. Multi-agent prerequisites

将来のmulti-agent化では、agent間の役割、ownership、lease、budget、tool policy、auditをmanifestとして明示する。共有mutable Controller/ToolRuntimeを前提にしない。

Phase 6のper-run ExecutionContext、ContextVarによるworker隔離、ToolRuntime.bound_to()、RuntimeState、AuditRecorderをこの将来境界の基礎として維持する。

## 30. Audit

Durable auditには、可能な範囲で次を含める。

- task/run/intent ID
- actor/authority
- provider/model/backend
- resource
- selected/fallback reason
- reservation and actual usage
- status transition
- error/reconciliation
- evidence reference

秘密値、credential、raw sensitive payloadはAuditへ保存しない。Redaction、byte cap、artifact referenceを適用する。

## 31. Metrics

Metricsは「実行回数」だけでなく、次の二つを区別する。

- useful-task objective
- resource objective

Provider、model、task class、privacy、quota domainなどの将来属性ごとに比較できるようにするが、今回Resource schemaを拡張しない。

## 32. Survival modes

運用状態は少なくとも次の3モードを持つ。

| モード | 挙動 |
| --- | --- |
| NORMAL | 通常のrouting、budget、retry |
| CONSERVE | 高コスト・高リスク処理を抑制し、有限の資源を温存 |
| SURVIVAL | provider outage/quota逼迫時に、許可された最小機能へ縮退 |

モード遷移はdurableで、復帰条件と人間のoverrideを定義する。

## 33. Provider outage tolerance

Provider outage、quota exhaustion、timeout、unknownを、無限retryや重複送信で隠さない。

- fallback候補はpolicy/capability/privacyに適合するものに限る。
- external dispatch後の不確実性はreconciliationへ送る。
- outage中の状態、選択、fallback、recoveryを監査する。

## 移行前調整での扱い

この章は将来のWorkflow、Evaluator、metrics、survival、multi-agent要件を
参照可能にするための保存である。Phase 7A/BのTask profileと決定的な
Intelligence Policy、明示opt-inのresource tier routing、Phase 7C/Dのhost evaluator、
独立したdurable event記録、有限なescalation plan coordinator、host/operatorによる
planの明示review（accepted/rejected）記録、受理済みplanのdispatch-ready handoff、
accepted handoffをcanonical ProviderDispatcherへ再検証付きで送る
`EscalationExecutor`は実装済みである。Phase 7Eのbounded workflow promotion
proposalも実装済みだが、自動promotion、Task lifecycleの自動循環、実績ベースrouting、
AgentBackend、MCPは今回の境界外である。
