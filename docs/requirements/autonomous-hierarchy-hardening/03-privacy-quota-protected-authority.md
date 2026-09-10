# 03 Privacy / Quota / Protected Authority

対象: D1–D3、E1、F1、I2、およびBilling / Qualification / Activationの境界

## D1 — Resource privacy projection

新規Resourceの `sensitivity="normal"` 固定で、sensitive Taskがremoteだけでなくlocalへも
route不能になる構造を解消する。Resourceのprivacy profileを明示的に持ち、Taskの
sensitivityとRouterのHard Filterへ伝播させる。

```text
Resource identity
  + privacy profile (remote / local / approved sensitivity)
  + current qualification
  + operator activation
  → Effective Resource Eligibility
```

Cloud Resourceをlocal-safeと推測してはならない。Ollamaは外部送信しないlocal Resource
として明示的に登録し、実測されたcapability / tierがある場合だけ通常routingへ参加させる。

## D2 — Ollama role / tier

Capability Matrixで `ollama / qwen3:8b` の `intelligence_tier` がnullの場合、モデル名や
一般的な評判からL1/L2を推測しない。次のいずれかを明示する。

- 実測qualificationを取得し、current tierを登録する
- `unclassified / survival-only` として通常exact-tier routingから除外する

用途はPRIVATE、SURVIVAL、offlineを優先し、通常時にモデルを常駐させない。実測なしで
normal OperationのL1/L2 fallbackと記録しない。

## D3 — child sensitivity monotonicity

parentよりchildのsensitivityを下げるdeclassificationを、通常のsubmit_childやModel
plannerから許可しない。

```text
sensitive parent → normal / public child: reject
internal parent  → public child: reject
normal parent    → normal / internal child: allow
```

declassificationが必要な場合は、Humanまたは専用Declassification Authorityによる
sanitized artifact、scope、reason、evidence付きの別経路とする。単なる文字列指定で降格
できない。

## Billing / Capability / Privacy / Activation

次のauthorityを一つの巨大catalogへ統合しない。

- Billing Authority: no-charge / paid / unknown、scope、期限
- Capability Qualification: observed能力、証拠、期限、tier
- Privacy Policy: data classificationと送信許可
- Operator Activation: 現在有効化されたbinding / model

最後にのみ、これらの積集合としてEffective Resource Eligibilityを作る。WorkerやModel
自己申告でいずれかを変更できない。

## E1 — UNKNOWN quota operating policy

`quota_status=unknown` をAVAILABLEと同義にしない。一方、exact binding/model、trusted
no-charge、current qualification、health、local bounded admissionを満たすResourceを、
quota telemetryが無いだけで永久NoRouteにも固定しない。

これを `UNKNOWN_QUOTA` operating modeとしてADR化する。

```text
trusted current no-charge + qualified + healthy
  → local bounded UNKNOWN admission
  → success: liveness / freshness更新、quota remainingはUNKNOWNのまま
  → 429/quota: quota:<domain>をBLOCKED、bounded requalificationまで除外
```

ローカル側のbounded concurrency、minimum interval、finite unknown-quota admissionsは
quota残量そのものではない。残量・reset時刻を捏造しない。「one bounded request」と
書きながら無制限に送信する実装にしない。

既知blocked、expired / unknown billing、未資格化、permission errorはUNKNOWN bootstrapで
迂回しない。429はblind retryせず `blocked_until` と原因を保存する。

## F1 — Protected authority surface

protected pathを単なるファイル名一覧でなくauthority responsibilityとして管理する。
最低限、次をWorker ownership不可とする。

- `spec/v2/GATE_STATUS.json`
- budget authority / `budget.py` / `budget_store.py`
- recovery policy / `recovery/`
- trusted billing authority（例: `resources/billing_catalog.py`）
- qualification matrix / runtime qualification projection
- provider activation policy
- outbound secret validation
- Host Verification policy
- Commander integration proof
- AgentBackend dispatch authority（例: `backends/dispatcher.py`）
- `resources/control.py`、`config/v2.yaml`等のResource / configuration authority
- credentials、`.env*`、private key、`.git`、`.devfarm`

DevFarmとCommanderが同じprotected responsibility policyを使う。Workerが自分をfree、
qualified、sandboxedに見せる変更、secret scanを無効化する変更、integration proofを弱める
変更を所有できない。

## I2 — Secret hygiene

`.gitignore`は `.env` だけでなく `.env.*` を対象にする。ただし共有テンプレートが必要な
場合は `!.env.example`等を明示する。既存tracked secretが無いことを確認し、CIのsecret
scanは導入する場合もread-only checkから始める。Credential値をfixture、manifest、auditへ
保存しない。

## 受入テスト

- sensitive remote Resource → reject
- privacy-qualified local Resource → accept
- sensitivity downgrade → reject
- Ollamaのtier未観測状態 →通常exact-tier routeへ入らない
- unknown quota + trusted current free →明示UNKNOWN policyでのみbounded admission
- unknown / expired billing、known blocked quota →bootstrap不可
- 429 → `BLOCKED_QUOTA` → bounded requalification
- protected responsibility pathへのWorker ownershipを拒否
