# 04 Operation / Lifecycle / Planner

対象: G1、G2、Gate C、Operation E2E、root decomposition

## G1 — Operationは既存Lifecycleを実際にcompositionする

Formal API仕様に記載された責務と `src/dev_agent/operation.py` のcompositionを一致させる。
Operation独自のstate machine、retry manager、Schedulerを作らない。

目標経路:

```text
OperationService
  → Controller
  → host evidence / existing Evaluator
  → FiniteLifecycleLoop
  → EvaluationCoordinator
  → reviewed EscalationExecutor
  → ProviderDispatcher
  → TaskLifecycleCoordinator
  → terminal / wait / reconciliation
```

既存のreview、effect intent、budget、quota、lease、UNKNOWN semanticsをそのまま通す。
Modelが失敗しただけでhigher tierへ勝手に昇格しない。higher-tier executionは既存の明示
reviewとbounded escalation authorityを保持する。

## Tierの実行順

Task profileは `minimum_tier`、`current_tier`、`maximum_tier` を分離する。

```text
root reasoning Task
  → initial current tier = L1 or L2 according to policy
  → exact current tierだけをRouterへ渡す
  → known retryable failureなら同Tier alternate
  → 明示review済みなら次tier
  → finite lifecycle limit内でterminal
```

UNKNOWN external outcomeはretry/escalationせず、reconciliationへ維持する。

## Operation E2E受入

通常の `OperationService` compositionで次を実証する。

```text
root/worker Task
  → L1 primary
  → known retryable failure
  → alternate L1
  → deterministic evaluation
  → explicit reviewed L2 escalation
  → L2 result
  → terminal transition
```

確認対象は、exact current tier、same-tier first、finite escalation、budget、effect intent、
provider audit、Task state、Queue state、process restartである。テスト側だけで
Evaluator / EscalationExecutorを手動compositionして成功扱いにしない。

## G2 — finite root decomposition

広いHuman-facing rootを、単純なkeyword classifierで無条件にL1へ送らない。rootは
planning / coordinationを担うL2相当として、既存のTaskType、risk、capability、privacy
authorityを使用する。

```text
broad root objective
  → bounded L2 planning proposal
  → Host validation
  → TaskGraphへchild作成
  → L0/L1/L2 childを適切に選択
```

Planner出力はauthorityではない。

```text
RootPlanningProposal
  ├ objective
  ├ children[]
  │   ├ objective
  │   ├ task_type
  │   ├ dependencies
  │   ├ required_execution_capabilities
  │   ├ policy traits
  │   ├ sensitivity
  │   ├ risk
  │   └ suggested execution target
  └ rationale
```

Hostがproposalを検証してから `submit_child()` と既存TaskGraphへ渡す。Planner自身はbudget、
approval、privacy、Gate、protected authorityを発行しない。

## Plannerの有限性とTaskGraph

既存 `max_depth`、`max_child_tasks`、`max_total_tasks_per_root`、cycle検出を利用する。
一回のproposalにもbounded child数・planning cycle数を適用する。PlannerがPlannerを再帰的に
生成する構造は作らない。

childのtierは全てL1に固定しない。

- deterministic child → L0
- narrow worker → L1
- reasoning child → L2
- protected / expert → reviewed higher tier

依存patchを必要とする後続Taskは、依存Taskが実際に `INTEGRATED` され、integration revisionを
baselineとして受け取るまでreleaseしない。artifactだけを受け取るTaskとは依存型を分ける。

Planner dependencyは依存ごとに次の型をdurably保持する。

- `TASK_COMPLETED`: 依存Taskが`COMPLETED`であること
- `ARTIFACT_READY`: 依存Taskがhost-validated artifactを`artifact_ready=true`として公開したこと
- `CODE_INTEGRATED`: `integration_status=INTEGRATED`かつ非空`integration_revision`が存在すること

旧形式の文字列dependencyは`TASK_COMPLETED`として読み、既存Taskのrestart/replay互換を
維持する。`CODE_INTEGRATED`は完了状態だけではreleaseしない。

## AgentBackend execution target

L3はL3 ModelProviderとCodex等のAgentBackendを混同しない。既存
`ExecutionTargetPolicy`、`ResourceRouter`、`ProviderDispatcher`、`AgentBackendDispatcher`
をcompositionし、次の軸で選択する。

- tier
- task type
- required autonomy
- tool access
- privacy
- backend capability
- budget
- approval

L3という理由だけでAgentBackendへ自動昇格しない。

## 受入テスト

- Operation入口からL1 primary → alternate L1 → reviewed L2 → terminal
- unknown external outcome → retry/escalationせずreconciliation
- root L2 → bounded planner proposal →複数child
- cycle、depth overflow、child count overflow → reject
- protected Task → Worker assignment reject
- TaskGraph上限をplannerが迂回しない
- parent/child sensitivity monotonicityを維持
- ModelProvider / AgentBackend target seamを越えて登録しない
