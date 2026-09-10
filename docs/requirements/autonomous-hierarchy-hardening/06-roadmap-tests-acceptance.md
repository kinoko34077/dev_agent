# 06 Roadmap / Tests / Acceptance

この章は、前章の要件を実装する順序、必須regression、文書同期、最終判定を固定する。

## 着手順

### Gate A — correctness / authority

1. Capability vocabularyをcanonical execution capability、qualification evidence、
   integration evidenceへ整理する。
2. Capability Matrixからcurrent qualification projectionを導出する。
3. Task execution capability、task competency、policy traitを分離する。
4. model名heuristicをProduction tier authorityから除去またはdebug pathへ隔離する。
5. Resource privacy profileをroutingへ投影する。
6. child sensitivityのmonotonicityとdeclassification authorityを実装する。
7. protected authority surfaceをbilling、qualification、activation、outbound secret、
   Host Verification、integration proof、AgentBackend dispatchまで再監査する。

### Gate B — liveness / concurrency

8. AgentBackend race typoをtyped errorへ修正する。
9. AgentBackend atomic dispatch claimをeffect intentへ接続する。
10. concurrent event pollingのdedupeを再現・修正する。
11. Provider execution timeout / orphan / circuitをbinding laneへ分離する。
12. saturation waitにcapacity wake authorityを接続する。
13. late responseをdurable intentからlifecycleへ戻す。
14. waiting reasonごとのwake authority契約を仕様・実装・testへ揃える。
15. scheduler claim countとlogical execution retry countを分離する。
16. UNKNOWN quota operating semanticsをADRと実装へ接続する。

### Gate C — actual hierarchy composition

17. Operationへ既存FiniteLifecycle、EvaluationCoordinator、EscalationExecutor、
    TaskLifecycleCoordinatorを実際にcompositionする。
18. 通常Operation入口でL1 primary → alternate L1 → reviewed L2をE2E実証する。
19. root L2の有限planner proposalを作る。
20. Host validation後にTaskGraphの既存制限を使ってchildを作る。
21. sensitive parentからの無承認declassificationを拒否する。

### Gate D — real-world proof

22. 実Cloud L1 WorkerをCommander経由でdogfoodする。
23. provider / binding / model / tier / task type / latency / usage / host verification /
    retry / accept-reject / Codex correctionをmetricsへ蓄積する。
24. `tests/v2`全回帰を実行する。
25. exact-head GitHub Actionsを確認する。
26. Current State、Traceability、API spec、implementation spec、DevFarm、Commander、
    requirements、必要ADRを同期する。

### Gate E — deployment hardening

27. GitHub ruleset / branch protectionを外部設定する。権限が無ければexternal blocked。
28. WindowsのHost Verification trust levelをcontainedからOS sandboxへ上げられるか調査する。
29. `.env.*`を含むsecret hygieneを確認する。

この後に初めて、Codex App Server実adapter、実AgentBackend E2E、MCPへ進む。

## 必須regression

### Capability / privacy

- qualified tool-capable Gemini + `tool_call` → route成功
- expired qualification → route失敗
- unqualified model名heuristic → Production tier routeなし
- unknown capability typo → submit / validationでfail-fast
- `architecture` trait → L2になるがResource capabilityへ入らない
- sensitive Task → remote normal Resource拒否
- privacy-qualified local Resource → accept
- sensitive / internal parent → public child拒否
- normal parent → internal child許可
- Ollama tier未観測 →通常exact-tier routeなし

### Quota / saturation / lifecycle

- trusted current no-charge + no telemetry → explicit UNKNOWN policyでbounded admission
- expired / unknown billing、known blocked quota → UNKNOWN bootstrap不可
- 429 → `BLOCKED_QUOTA` → bounded requalification
- Provider hang →当該binding laneだけsaturated
- 他bindingは選択可能
- orphan完了またはrestart reconciliation → matching saturation wait wake
- saturation waitでlogical retry budget消費なし
- late success →新Requestなしでdurable lifecycle continuation
- unknown external outcome → retry/escalationせずreconciliation

### AgentBackend concurrency

- race pathがNameErrorでなくtyped error
- 同一dispatch_idを2 callerで競合 → `backend.start`一回
- create intent / pending / prepared race →外部start一回
- session persist直前crash →重複開始せずdiscover / reconcile
- winner sessionをloserがreplay、unknownならreconciliation
- concurrent event pollで同一sequence重複なし

### Operation / planner

- Operation入口 → L1 primary → alternate L1 → evaluator → reviewed L2 → terminal
- root L2 →有限proposal →複数child
- cycle、depth、child count overflow → reject
- protected Task → Worker assignment reject
- TaskGraph上限迂回なし

### Development proof / recovery

- Commanderが2〜3の独立narrow Taskを実L1へ委譲
- Host Verification、Codex review、Git-backed integrationを完了
- metricsへ実測を保存
- Commander、Host Verification、Provider、AgentBackend、quota requalificationの
  crash/restartで重複effectなし
- full `tests/v2` PASS、exact-head CI PASS

## Formal specification同期

実装または判定が変わったら、次を同じ作業単位で確認する。

- `docs/CURRENT_STATE.md`: 現在のimplementation baseline、test、live / blocked状態
- `docs/SYSTEM_MAP.md`: 所有者・依存方向・公開入口
- `docs/CODEX_COMMANDER.md`: delegation-first、ownership、integration evidence
- `docs/DEVFARM.md`: outbound scope、Host Verification trust level、worker eligibility
- `spec/v2/05_api_spec.md`: public contractとOperation compositionの実装状態
- `spec/v2/06_implementation_spec.md`: directoryとdependency direction
- `spec/v2/TRACEABILITY.md`: requirement → implementation → test → evidence
- `docs/requirements/`: 要件の章indexと本Gateの位置付け
- `spec/v2/adr/`: capability taxonomy、UNKNOWN quota、timeout/saturation、child privacy、
  Host Verification trust levelの決定理由

実装前の差分は `PLANNED` / `PARTIAL` と記録し、実装・focused test・full regression・
external evidenceが揃うまで `VERIFIED` と書かない。G6O1、実Cloud資格化、branch protection、
OS sandboxなど外部条件をlocal testから推測しない。

## 禁止事項

- LangGraph、CrewAI、Temporal等によるKernel置換
- A2A、AG-UI、Virtual Office UI、MCP、Codex App Server実adapterの先行着手
- 新しいScheduler、StateStore、Budget、Retry frameworkの並立
- Capability / Billing / Privacy / Qualification / Activationを一つの巨大設定へ統合
- Model自己申告によるtier、qualification、billing、privacy、authorityの変更
- UNKNOWN effect / quotaのblind retry
- protected authorityや公式branchへの自動merge

## 最終acceptance criteria

- [ ] Resource routing capabilityが `text` 固定ではない
- [ ] current qualificationからcanonical capabilityを導出する
- [ ] expired qualificationをroutingへ使わない
- [ ] model名だけでProduction tierを推測しない
- [ ] task traitとprovider execution capabilityを混同しない
- [ ] remote/local privacy profileがResource routingへ反映される
- [ ] sensitive childの無承認declassification不可
- [ ] Ollamaのrole/tierが実測またはsurvival-onlyとして明示される
- [ ] Provider hangが全poolを止めない
- [ ] saturation待ちにwake authorityがある
- [ ] late completionがTaskを永久waitingにしない
- [ ] scheduler claimとlogical retryを混同しない
- [ ] AgentBackend race typoがない
- [ ] 同一dispatchのstartが一回にfenceされる
- [ ] AgentBackend unknown semanticsが維持される
- [ ] UNKNOWN quotaの意味がADRと実装で一致する
- [ ] “one bounded request”と称する無制限送信がない
- [ ] quota値を捏造しない
- [ ] trusted billing / qualification / admissionをWorkerが所有できない
- [ ] Host Verificationをsandboxと誤称しない
- [ ] normal OperationがEvaluator / Lifecycleを実際に使う
- [ ] L1 → alternate L1 → reviewed L2がOperation E2Eで成立する
- [ ] root L2から有限child proposalを生成できる
- [ ] TaskGraph limitsを迂回しない
- [ ] 実Cloud L1 WorkerをCommander経由でdogfoodする
- [ ] Host VerificationとGit-backed integrationを完了する
- [ ] metricsを記録する
- [ ] full tests/v2 PASS
- [ ] exact-head GitHub Actions PASS
- [ ] Current State / Traceability / formal specを同期する

## 到達する実行像

### Development

```text
Human
  → Codex Commander
  → bounded decomposition
     ├ architecture / security / integration → Codex
     └ narrow work → Free L1 Worker
                       → isolated proposal
                       → Host Verification
                       → Codex review
                       → Git-proven integration
```

### Production

```text
Broad root Task
  → L2 bounded planner
  → Host-validated TaskGraph
  → L0 / L1 children
  → same-tier free-provider fallback
  → deterministic evaluation
  → reviewed L2/L3 escalation only when needed
  → terminal result
```

Provider hangは当該binding laneだけを隔離し、outcomeがknownなら安全なfallback、unknown
ならreconciliationへ送る。Sensitive rootは自動declassificationせず、privacy-qualified
local executionまたは明示authorityを要求する。この状態をもって、下層Modelを置いただけで
なく、上位が管理し、下位が実働し、故障時にも安全に復旧する階層型Agentと判定する。
