# Autonomous Hierarchy / Cross-cutting Hardening Audit

Status: ACTIVE IMPLEMENTATION INPUT

対象Repository: `kinoko34077/dev_agent`  
対象Branch: `v2/bootstrap`

この章群は、既存のResource / Provider、Intelligence hierarchy、DevFarm /
Commander、FiniteLifecycle、AgentBackend、TaskGraphを一つの実用経路へ接続する
ための横断監査・修正仕様である。新しいAgent framework、Scheduler、StateStoreを
追加する仕様ではない。

## 基準と文書の優先順位

- 監査基準HEAD: `ed2177a38f479a5e4a031829909ef85f8aff506f`
- 実装基準: `ead4dfef38543e1783ff36900e7bdf23e0a958cd`
- GitHub Actions: `v2-core` Python 3.10 / 3.11、`v2 tests` はいずれもPASS
- Current State記録: `563 passed, 1 skipped`
- 実装状態・Gate判定の正本: `docs/CURRENT_STATE.md`、`spec/v2/GATE_STATUS.json`、`spec/v2/TRACEABILITY.md`
- 本章群: 今回のhardeningの要求・受入条件を保持する作業仕様。未実装要件を実装済みと扱わない

既存文書と本章群の表現が異なる場合、現在の実装状態はCurrent State / Gateを、
今回の作業範囲と受入条件は本章群を参照する。Gate statusは根拠のない昇格・降格を
行わず、実装後に対応するevidenceを追加してから同期する。

## 選択的ロード

必要な作業だけを読む。全体を読む必要がある場合も、まず本indexを読み、該当章を
順に開く。

| 章 | 範囲 | 主な参照目的 |
| --- | --- | --- |
| [01 Capability / Qualification / Tier](01-capability-qualification-tier.md) | A1–A4 | routing capability、資格化projection、task trait、tier authority |
| [02 Liveness / Concurrency / Wake](02-liveness-concurrency-wake.md) | B1–B4、C1–C3、待機復帰 | provider hang、saturation、late completion、AgentBackend race、counter分離 |
| [03 Privacy / Quota / Protected Authority](03-privacy-quota-protected-authority.md) | D1–D3、E1、F1、I2 | privacy projection、Ollama、UNKNOWN quota、protected path、secret hygiene |
| [04 Operation / Lifecycle / Planner](04-operation-lifecycle-planner.md) | G1–G2 | Operation composition、L1→L2、root planner、TaskGraph validation |
| [05 Host Verification / External Proof](05-host-verification-external-proof.md) | H1、I1、J1–J2 | containment trust level、Cloud Worker dogfood、外部Gate、Backend admission |
| [06 Roadmap / Tests / Acceptance](06-roadmap-tests-acceptance.md) | Gate A–E、受入条件 | 着手順、必須regression、docs同期、完成判定 |

## 不変条件

- Codex App Server実adapter、MCP、Virtual Office UIは本Gate完了前に着手しない。
- `Controller`、`ProviderDispatcher`、`ProviderRegistry`、`ResourceControlPlane`、
  `DurableQueue`、`WorkerRunner`、`SQLiteStateStore`、`TaskLifecycleCoordinator`、
  `FiniteLifecycleLoop`、`QuotaWakeScheduler`、`DevFarmOrchestrator`、
  `CommanderPlanStore`を再実装しない。
- Resource、Billing、Capability、Privacy、Qualification、Activationは別authority
  として保持し、最終的なEffective Eligibilityでのみ合流させる。
- UNKNOWNな外部結果・quota・資格化・課金情報を、成功・無料・利用可能と推測しない。
- 公式branchへの自動merge、protected authorityのWorker所有、unknown effectのblind retry、
  無限retry、Model自己申告によるtier/資格変更を許可しない。

## 横断目標

```text
上位Control Plane
  → Task profile / minimum sufficient tier / authority
  → qualified Resource / Provider binding
  → 下位ModelまたはAgentBackendの実働
  → deterministic Host Verification / Evaluator
  → PASS、または有限same-tier fallback / reviewed escalation
  → durable audit / Git evidence / crash-restart recovery
```

各章の実装完了は、対応するfocused test、full `tests/v2`、exact-head CI、
Current State / Traceability同期で確認する。実Cloud、GitHub ruleset、OS sandbox、
paid Providerなど外部条件は、コードで成功扱いにせず明示的にBLOCKED_EXTERNALまたは
DEFERREDとして記録する。
