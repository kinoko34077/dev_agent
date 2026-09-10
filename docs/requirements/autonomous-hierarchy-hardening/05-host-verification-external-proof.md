# 05 Host Verification / External Proof

対象: H1、I1、J1、J2、AgentBackend admission実接続証明

## H1 — Host Verificationのtrust level

現行のisolated Git worktree、sanitized environment、temporary HOME、bounded stdout/stderr、
process-group containment、timeout、process-tree killは維持する。ただしGit隔離だけではOS
filesystem/network sandboxではない。

現在の信頼レベルは `HOST_CONTAINED` とし、`OS_SANDBOXED`と呼ばない。

最高信頼のunattended external Worker modeを設ける場合は、Windowsを第一対象として、可能な
範囲で以下を追加調査・実装する。

- restricted OS identity
- filesystem isolation
- network deny-by-default
- process / resource limit
- credentialを継承しない環境

Docker常駐や巨大なsandbox frameworkを標準依存にしない。提供できない隔離をsandbox済みと
証明しない。fallbackは `contained-but-not-sandboxed` と明示し、unattended最高信頼modeへ
昇格させない。

## J1 — 実Cloud WorkerをCommander経由でdogfood

現在のlocal deterministic Commander dogfoodだけでは、Commanderと実Cloud Workerの完走を
証明しない。次の実開発sliceで、qualification、billing、expiry、operator activationを
送信直前に再確認する。

```text
Codex Commander
  → 独立ownershipのnarrow Taskを2〜3件作成
  → Gemini 3.5 Flash-Lite / Cloudflare / OpenRouterのqualified L1 Worker
  → Host Verification
  → Codex review
  → Git-backed INTEGRATED
```

Worker候補は、focused test、fixture、parser、docsなどの限定Taskにする。authority、budget、
recovery、protected path、cross-cutting integrationはCodex担当に残す。Worker同士は直接通信
せず、metricsへprovider、binding、model、tier、elapsed、usage、host test、retry、accept/reject、
Codex correctionを記録する。

## J2 — Evidence Routing

`EvidenceBasedRoutingPolicy`は維持するが、実Providerのminimum sample、freshness、acceptance、
retry、rollback evidenceが揃うまで `DEFERRED_ADVISORY` とする。実績はhard constraintを上書き
しない。実sample不足を理由に、実績ベースroutingを完成済みと記録しない。

## GitHub external Gate

`v2/bootstrap`のbranch protection / rulesetはrepo内コードでは代替できない。外部設定が
可能なら次をrequired checkとして検討する。

- `v2-core / Python 3.10`
- `v2-core / Python 3.11`
- `v2 tests`
- force push禁止
- branch deletion禁止

権限不足の場合は `BLOCKED_EXTERNAL / USER_ADMIN_ACTION` と記録し、Gateをコードで偽装しない。

## AgentBackend admissionの実接続

Fake callbackがTrueを返しただけでは、実authorityのE2E証拠にならない。実Codex adapter前に、
既存Queue lease、Budget authority、Approval、Privacy policyから、typed `BackendAdmission`
が実際に解決される境界をテストする。新しいAuthorityは作らない。

AgentBackendはTask state、Scheduler、Budget、Approval、Recovery、Gateを所有せず、
`AgentBackendDispatcher`のeffect intent、session、event、result、cancel、unknown、
reconciliation境界を通過する。

## 実装順の制約

このGateが閉じるまで、次を着手しない。

- Codex App Server実adapter
- MCP
- A2A / AG-UI / Virtual Office UI
- Self-Improvement
- 新しいAgent framework / Scheduler / StateStore / Budget system

## 受入テスト

- qualified実Cloud L1をCommanderから2〜3独立Taskへdispatch
- Host Verificationを通過し、Codex review後にGit revisionでintegrationを証明
- Worker metricsをdurable artifactへ記録
- unqualified / expired / billing unknown Workerを送信前拒否
- Host Verificationをsandboxと誤称しない
- branch protectionが未設定なら外部blockedとして明示
