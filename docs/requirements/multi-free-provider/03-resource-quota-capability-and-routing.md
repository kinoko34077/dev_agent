# 03 Resource, Quota, Capability, and Routing

要件範囲: 7-14

## 7. Model Resource Pool

### RES-001 Multi-Provider前提

通常運転を単一Providerへ依存させない。初期構成目標は、Gemini、Groq、Cloudflare Workers AI、Mistral、OpenRouter Freeを通常Cloud、SambaNova等を補欠、OllamaをLocalとする。Provider名は初期ロードマップであり恒久仕様ではない。追加・削除でCore Contractを変更しない。

### RES-002 Localの位置付け

Local Modelは通常Mainから降格し、sensitive data、credential-adjacent処理、Cloud/network outage、SURVIVAL、Recovery、Provider障害、offline処理を主用途とする。性能・Resource条件が変化した場合に通常Resourceへ再昇格できる構造を維持する。

## 8. Resource Identity

provider、model、credential、resource、quota domainを混同しない。

~~~
provider      = gemini
model         = flash-x
credential    = key-a
resource      = gemini-project-main
quota_domain  = google-project-123
~~~

## 9. Quota Domain

### QUOTA-001 一級概念

複数Credentialが同一quotaを共有することを表現する。credential_id と quota_domain は異なる概念であり、同じquota domainのCredentialを独立した残quotaとして加算しない。

### QUOTA-002 状態

最低限、quota_domain_id、provider_id、scope_type、observed_at、confidenceを保持する。必要に応じてmetric、unit、window、request_limit、request_remaining、token_limit、token_remaining、limit、remaining、consumed、reset_at、reset_source、blocked_until、block_reason、daily_remaining、concurrency_limitを保持する。Providerごとの単位を無理に統一しない。

### QUOTA-003 Observation

無料枠、Rate Limit、Model Availabilityをコードへ固定せず、公式値、Provider response header、管理画面値、429観測、manual configurationなどからResource LedgerへObservationとして登録する。source、observed_at、confidence、expires_atを保持可能にする。

## 10. Resource Observation

### RES-003 Router用観測値

Resourceごとにavailability、health、quota_remaining_ratio、quota_reset_at、inflight、concurrency_limit、latency_ewma_ms、failure_ewma、capabilities、privacy_level、effective_cost、observed_atを扱えること。利用不能な値はunknownとする。

## 11. Provider Capability

### CAP-001 Model単位

CapabilityはModel単位で観測する。例はtext、tool_call、structured_output、json、long_context、code、reasoning、architecture、review、multilingual。Providerの公称対応とdev_agentで実際に検証済みであることを区別する。

### CAP-002 Capability Matrix

Model ResourceのCapability Matrixではspecified、observed、qualified、expired、blockedを区別する。Model/Provider変更後に過去qualificationを無期限利用しない。

## 12. Router

### ROUTE-001 Hard Filter

scoreより先にrequired capability、privacy、health、circuit state、quota availability、observation freshness、budget、survival policy、explicit provider restrictionsを判定する。一つでも満たさないResourceは除外する。

### ROUTE-002 Soft Score

Hard Filter後にexpected_success、effective_cost、quota headroom、latency_ewma、failure_ewma、inflight、provider diversityなどで選択する。固定の万能ランキングを作らない。

### ROUTE-003 Task別成功率

将来、task_type × model/resourceごとの実績を記録し、総合Model評価ではなくTask適合度を利用する。

## 13. Free-First Policy

### COST-001 通常優先順位

L0 deterministic、Free Cheap、Free Core、Subscription Allowance、Paid API、High-cost Expertの順を原則とする。ただし安価なModelの失敗で総コストが上がる場合は上位へEscalateする。

### COST-002 Expected Total Cost

単価だけでなく、次を概念上考慮する。

~~~
expected_total_cost
= attempt_resource_cost × expected_attempts
  + failure_cost + escalation_cost
~~~

## 14. Escalation

### ESC-001 Ladder

L0 -> L1 -> L2 -> L3を基本とし、失敗・不確実性・難度・リスクに応じて上げる。成功実績が十分なら下層へ降格可能にする。

### ESC-002 有限性

Taskごとにmax_attempts、max_escalations、max_cost、deadlineを持ち、失敗を理由に無制限に高性能Modelへ再送しない。

## 現行実装への適用境界

Phase 6A第一バッチで、ResourceLedger schema v8へ quota_domain、durable quota observation、generic `unit`（requests／tokens／neurons）、metric、window、limit／remaining／consumed、authority、reset_at／reset_source、blocked_until／block_reason、inflight、latency_ewma_ms、failure_ewma、concurrency_limit、quota_remaining_ratio、quota_reset_atを追加した。Routerはquota domain付きResourceについてfresh quota observationをhard filterし、同じprivacy条件ではquota headroomをcostより先に優先する。429／quota／transportのtyped ProviderErrorは、明示resetまたはProvider-neutral policyのbounded cooldownとblock reasonへ変換し、古いblocked observationを時刻経過だけで復活させない。同じquota domainの複数Resourceは観測を加算せず、freshな残量比率の最小値を共有domainの保守的headroomとして扱う。concurrency_limitもdispatch前のhard filterとする。Provider応答はAdapterが正規化した `usage.quota_observation` に限りLedgerへ取り込める。公式値と推定値は `authority`／`confidence`／`source` で区別し、CloudflareのNeuron消費推定は残量観測へ昇格させない。

未実装の後段要件は、未資格化Providerの固有header観測、完全なFree-first escalation、provider diversity scoring、およびResourceRouterへの自動接続である。quota domainの集約は意図的に保守的な最小headroomに限定し、Credential残量の加算は行わない。Geminiのbinding/tier選択は現行のbounded policyに含む。host-verified Worker metricsには、`EvidenceBasedRoutingPolicy`がminimum sample、証拠期限、受入率／retry rollback条件を適用し、呼出側が先にhard filterしたbindingだけをadvisory順位付けする。証拠不足・期限切れ・回帰は推奨しないが、このpolicyはResourceRouterのcapability／privacy／quota／budget判断を置換しない。
