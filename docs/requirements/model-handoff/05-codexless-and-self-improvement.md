# Codex-less routine candidates and Self-Improvement contracts

この章は、D7の低リスク候補判定とD8のF0〜F2データ境界を定義する。
現在の実装状態・live evidence・Gate昇格は`docs/CURRENT_STATE.md`、
`docs/V2_DETAILED_ROADMAP.md`、`spec/v2/evidence/**`を正本とする。

## D7 — Codex-less routine candidate

- Codex-less候補は、複数の異なるTask/attemptによるcleanなFree L2 Reviewer Shadow
  比較Evidenceが既定の最小数を満たした後だけ評価できる。
- 対象は既知のbounded task class、`LOW`/`NORMAL`相当のrisk、非protected path、
  安全なsensitivity、Worker-owned Taskに限定する。
- `HOST_VERIFIED`、独立検証、attempt単位のHost trust/approval、ReviewPacketとの
  evidence groundingをHost側で再確認する。モデルの自己申告は代替証拠にしない。
- 判定結果はroutine candidateであり、Reviewerへdecision authorityを与えず、
  公式branchの自動merge、push、Gate昇格、無条件integrationを行わない。
- Shadow evidenceが不足、比較が不一致、scope/risk/sensitivity/verificationが不適合、
  またはunknownなTask classの場合はfail closedでCodex reviewへ戻す。

## D8 — F0〜F2 proposal-only records

- F0 Observationは、Hostが観測したbounded scalar metricsとartifact/evidence reference
  だけを保持し、raw Worker output、conversation、patch、stdout/stderr、credentialを
  保持しない。
- F1 Diagnosisは、供給されたObservationのevidence referenceにgroundedな解釈として
  表現し、Human仕様や安全Authorityへ自動昇格しない。
- F2 Improvement PlanはObservation/Diagnosisを参照するproposal-only recordであり、
  `requires_human_approval=true`を維持する。dispatch、Task mutation、integration、
  approval authorityを持たない。
- F0〜F2は既存DevFarm、Commander、Scheduler、Budget、Host Verification、
  Controlled Repairを置換せず、これらへ接続する場合も既存Authorityを通る。

### D8 Host composition boundary

- Hostは既存Supervisorのcompact plan/ReviewPacketからF0 Observationを作成し、
  既存のF1/F2 contractへ接続できる。出力は`.devfarm/self-improvement/`配下のbounded
  append-only artifactに限り、Commander plan、Git、Task、Worker dispatchを変更しない。
- このcompositionはmodel-driven diagnosis、automatic repair、approval、integrationの
  authorityを与えない。実行を伴う次工程は、別途Human/Host Gateと既存DevFarm境界を通る。

## D9 — Controlled Self-Repair candidate boundary

- D9の最初の段階は、F2 `PROPOSAL_ONLY` planとHostが収集したbounded evidenceから、
  repair candidateの採否を決定的に評価することに限る。候補にはpatch本文を含めず、
  patch/manifest/verificationのreference、digest、base revision、変更path、rollback
  referenceだけを保持する。
- 候補成立には、独立Host Verification、known external outcome、attempt単位のtrust/approval、
  protected path不在、安全なrisk、rollback referenceを要求する。UNKNOWN、検証不備、
  protected path、rollback欠落はfail closedする。
- 候補は常に`PROPOSAL_ONLY`かつHuman approval必須である。D9 policyはpatch実行、
  approval消費、rollback、Task mutation、Git integrationを行わず、既存DevFarmと
  Host/approval/Recovery authorityを置換しない。
- `RepairExecutionRequest` / `RepairExecutionPolicy`は、候補・verified attempt・
  durable review reference・target・external-write approvalのexact argument hashを
  read-onlyに束縛するpreflightである。preflightはapprovalを消費せず、既存
  SupervisorのHost integration/rollback authorityへ渡す準備状態だけを返す。
