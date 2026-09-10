# 01 Capability / Qualification / Tier

対象: A1、A2、A3、A4

## 目的

Providerが実際にできること、資格化で観測した証拠、Taskが求める能力、Policyが
要求する知能tierを別概念として扱う。Resource RouterのHard Filterへは、資格化
証拠やTaskの抽象的competencyをそのまま渡さず、canonical execution capabilityの
projectionだけを渡す。

## A1 — Operation Resource capability

OperationServiceが新規Resourceを `capabilities=["text"]` 固定で登録してはならない。
`tool_call`を必要とするTaskが、ToolCallまで資格化済みのProviderをNoRouteにする
構成を解消する。

ただし、Capability Matrixの全文字列をResourceへコピーしない。Resourceへ投影する
値は、現在有効な資格化証拠からResolverが導出したcanonical execution capabilityに
限る。既存Resourceのoperator-owned identity / policyをOperation起動でupsertしない。

## A2 — Qualification概念の分離

Capability Matrixに混在し得る情報を次の3群へ分離する。

### Routing capability

RouterのHard Filterに使用できる実行能力。

- `text`
- `tool_call`
- `structured_output`
- `json`
- `long_context`

語彙はcanonicalに固定し、Task / Resource / Qualificationで同じ意味に同じ名前を使う。

### Qualification evidence

能力を証明する低レベル観測。

- `model_generated_tool_call`
- `tool_result_roundtrip`
- `final_response`
- `controller_e2e`

### Integration / invariant evidence

Model能力ではなく、dev_agent側の不変条件を証明する証跡。

- `thought_signature_roundtrip`
- `durable_provider_audit`
- `budget_reconciliation`

`durable_provider_audit`等をProvider Resource capabilityとしてRouterへ登録しない。

## Qualification projection

既存Matrix schemaを直ちに破壊せず、次の意味を持つresolverまたはprojectionを追加する。

```text
ProviderQualification
  ├ routing_capabilities       # canonical、Router用
  ├ qualification_evidence    # raw observed evidence
  ├ integration_evidence      # system invariant evidence
  ├ provider_id
  ├ provider_binding_id
  ├ model_id
  ├ intelligence_tier
  ├ tested_at
  ├ expires_at
  └ confidence

raw evidence
  → QualificationResolver
  → current, unexpired routing capabilities
  → Effective Resource Routing View
```

資格化期限切れ・未資格化・confidence不足は、routing capabilityを導出しない。
Resource catalogの静的identityを資格化更新で上書きせず、`Resource policy ∩ current
qualification`をEffective Routing Viewとする。

## A3 — Task capability / competency / policy trait

次の3種類を分離する。

| 種別 | 例 | 使用先 |
| --- | --- | --- |
| provider execution capability | `text`、`tool_call`、`structured_output` | ResourceRouter Hard Filter |
| task competency | `code`、`architecture`、`review`、`multilingual` | Task classification / tier policy |
| policy or risk trait | `security_sensitive`、`protected`、`recovery` | approval、privacy、ExecutionTarget、risk |

`architecture`、`protected`等をProvider capabilityとしてResourceへ要求しない。
`Task.required_capabilities`の旧値はversioned resolverで分類し、不明な語彙はsubmitまたは
validation時にfail-fastする。黙ってNoRouteにしない。

既存の `_CAPABILITY_MINIMUMS` による `architecture → L2` 等のPolicy判定は維持するが、
同時にResourceへ `architecture` capabilityを要求してはならない。

## A4 — Tier authority

Production routingではmodel名のheuristicをtierの正本にしない。

```text
qualified exact provider/binding/model
  → current, unexpired qualification
  → intelligence_tier
```

`gemini-3.7-flash`のように未資格化candidateを、名前だけでL2としてProductionへ
routeしてはならない。debug / manual qualification pathでの推定はProduction poolから
分離する。expired / unknown qualificationはProduction tierとして使わない。

Task側の `minimum_tier`、今回の `current_tier`、escalationの `maximum_tier` は分離する。
Router初回候補はcurrent exact tierだけにする。higher tierへの移行は既存の明示reviewと
bounded escalation authorityを経由する。

## 受入テスト

- qualifiedなtool-capable Gemini + Task `tool_call` → canonical route成功
- expired qualification → routing失敗
- 未資格modelの名前heuristic → Production tierとしてrouteしない
- unknown capability typo → submit / validationでfail-fast
- `architecture` Task → L2へなるがResourceへ `architecture`を要求しない
- Matrix変更、projection変更、qualification admission policyはWorker ownership不可
- Resource static rowをOperation起動やderived qualificationで勝手に変更しない
