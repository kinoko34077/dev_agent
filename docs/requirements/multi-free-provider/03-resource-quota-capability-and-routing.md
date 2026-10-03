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

### QUOTA-004 No-charge replenishing quota

明示的にno-chargeかつ期間回復型であることが確認されたResourceでは、Provider自身がauthoritativeなremaining値を公開していないことだけを理由にformal admissionを拒否しない。公式のallowance、reset semantics、Provider responseで観測したusage又は安全側の換算値をdurableなperiod ledgerへ累積し、`derived_conservative`等の非authoritative authorityとして残量を保守的に導出してよい。

この経路では、process restartだけで同一periodの消費を失わないこと、消費量を安全側へ丸めること、Providerのquota exhaustion signalをローカル推定より優先してresetまでrouteを停止すること、paid fallback又はbilling/permission mutationを起こさないことを必須とする。unknown billing又はpaid overageの可能性があるResourceへこの緩和を適用しない。

## 10. Resource Observation

### RES-003 Router用観測値

Resourceごとにavailability、health、quota_remaining_ratio、quota_reset_at、inflight、concurrency_limit、latency_ewma_ms、failure_ewma、capabilities、privacy_level、effective_cost、observed_atを扱えること。利用不能な値はunknownとする。

### RES-004 Model Catalog

Providerの公式model-list API等から得た利用可能モデルは、明示的なread-only discoveryで、provider/binding/modelのexact identity、source、observed_at、expires_atを持つModel Catalogへ記録できる。discoveredであることだけでは実行許可にならず、refreshはcredentialの自動activateやtrusted routing snapshotの暗黙更新を行わない。Provider追加時にCoreの固定モデル一覧を変更する必要がない構造を維持する。

### RES-005 Provider Connection

通常運転のProvider接続は、Provider固有のAPIキー値そのものではなく、provider_id、credential binding、credential reference、account/project scope、quota domain、endpoint/discovery profile等の非secret identityで表現する。provider、credential、model、execution binding、quota domainを別identityとして保持し、Model変更だけでCredential又はquota domainが変わったものとして扱わない。

Credential値の解決、認証header、account/projectを含むProvider固有endpoint、model-list request、generation request/response及びusage/error normalizationはProvider/Discovery Adapterの責務とする。CoreのResource/Router層へProvider固有payload形式を漏らさない。

### RES-006 Unified model candidate path

通常Operationは、各Provider Connectionから現在のModel inventoryを取得又はfreshなModel Catalogを参照し、共通のcandidate materializationへ渡す。Model Catalogのentryは、specialized-model exclusion、exact qualification/capability evidence、current billing policy、privacy、quota、healthを通過して初めてexecution candidateになれる。

未pinのconnectionではcurrent admitted candidatesからRouter/selection policyが選択する。Human又は設定によるexact model pinも同じadmissionを通し、現在のProvider inventoryでidentityを確認できないpin、又はinventory read自体が成立しないpinはfail-closedとする。名前の類似性によるsilent substitutionを行わない。

選択後はprovider_id、credential binding、model_id、quota domainを保持したdeterministic execution bindingを構成し、ProviderFactoryへexact selected modelを渡す。ProviderFactory自身はModelを選定又は推測しない。

## 11. Provider Capability

### CAP-001 Model単位

CapabilityはModel単位で観測する。例はtext、tool_call、structured_output、json、long_context、code、reasoning、architecture、review、multilingual。Providerの公称対応とdev_agentで実際に検証済みであることを区別する。

### CAP-002 Capability Matrix

Model ResourceのCapability Matrixではspecified、observed、qualified、expired、blockedを区別する。Model/Provider変更後に過去qualificationを無期限利用しない。

### CAP-003 Evidence layer separation

Model Catalog（API上の存在）、Benchmark Catalog（性能）、Capability Catalog（実行能力）、Runtime State（quota、health、latency、failure等）は別の証拠層として保持する。Hostのadmissionは、exact current model evidenceと既存のqualification、billing、privacy、quota、health、explicit binding policyを合成し、discovery・benchmark・capabilityのいずれか単独からrouting候補を昇格させない。期限切れ、欠落、曖昧なidentityはfail-closedとする。

## 12. Router

### ROUTE-001 Hard Filter

scoreより先にrequired capability、privacy、health、circuit state、quota availability、observation freshness、budget、survival policy、explicit provider restrictionsを判定する。一つでも満たさないResourceは除外する。

### ROUTE-002 Soft Score

Hard Filter後にexpected_success、effective_cost、quota headroom、latency_ewma、failure_ewma、inflight、provider diversityなどで選択する。固定の万能ランキングを作らない。

### ROUTE-003 Task別成功率

将来、task_type × model/resourceごとの実績を記録し、総合Model評価ではなくTask適合度を利用する。

### ROUTE-004 Model selection authority

Model discoveryはinventory factでありrouting authorityではない。dynamic selectionはHard Filterを通過したcurrent candidate間でのみ行う。operator pinは優先指定であってadmission bypassではない。Providerごとのdefault model名をCore routing authorityとして使用せず、bootstrap seedが必要な場合もcurrent discoveryとdownstream admissionを迂回させない。

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

Issue #50で、no-chargeかつ期間回復型のquotaについてauthoritative remaining APIをformal admissionの必須条件から外し、durableなconservative period ledger、provider/local exhaustion hard-stop、reset-boundary recoveryを受入済みとした。CloudflareのProvider-reported Neuron consumptionは観測usageとして保持し、tokenからの換算値又は残量導出はauthorityを分離する。成功応答でbounded usageを得られない場合はstale positive headroomを再利用せず、既存reset boundaryまで保守的にquarantineする。

Issue #52 C1-C5で、canonical Providerの通常configured Operation laneを共通のProviderModelDiscovery -> Model Catalog -> candidate materialization -> exact admission -> runtime binding経路へ統一した。対象はGemini、Cloudflare Workers AI、OpenRouter、Groq、Mistral、SambaNova、Ollama local、Ollama Cloud、Vercel AI Gatewayである。未pin laneはcurrent discoveryから展開し、explicit model pinもcurrent inventory確認を必須とする。discovery失敗、stale/removed pin、unknown/paid billing、qualification不足、期限切れ証拠はfail-closedであり、Provider固有のmodel defaultはrouting authorityにならない。

未実装の後段要件は、未資格化Providerの固有header観測、完全なFree-first escalation、provider diversity scoring、Benchmark/Model Catalogの定期更新自動化である。quota domainの集約は意図的に保守的な最小headroomに限定し、Credential残量の加算は行わない。host-verified Worker metricsには、`EvidenceBasedRoutingPolicy`がminimum sample、証拠期限、受入率／retry rollback条件を適用し、呼出側が先にhard filterしたbindingだけをadvisory順位付けする。証拠不足・期限切れ・回帰は推奨しないが、このpolicyはResourceRouterのcapability／privacy／quota／budget判断を置換しない。
