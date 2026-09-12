# 内部 API 仕様（Phase 7 統合境界）

この文書は、現行 v2 の公開境界を定義する正式契約である。開発者向けの配置索引は `docs/SYSTEM_MAP.md`、現在の証跡は `docs/CURRENT_STATE.md` が担う。ここに記載のない内部実装は公開契約ではない。

## 共通原則

- Provider SDK 型、HTTP payload、Agent/OSS 固有型はこの境界へ漏らさない。
- 外部副作用は durable effect intent、budget、approval、lease/fencing、audit を通過する。結果不明時は `UNKNOWN` / reconciliation を維持する。
- API が実装済みであることだけでは Gate の VERIFIED を意味しない。
- `StateStore`、Queue、Resource、Recovery の正本を二重化する実行系を作らない。

### `HandoffEnvelope`

- 責務: Human、Planner、Executor、Reviewer間のmodel-neutralな情報受渡しを、ControlとPayloadに分けて表現する。
- 公開入口: `HandoffEnvelope(...)`、`validate_handoff(...)`、`render_handoff(..., renderer="kinotch-ja-v1")`。
- 入力/出力: subject、instruction、conditions、cautions、requirements、roles、payload、payload mode、reference、compression provenance、optionalな`HandoffDirective`。Directiveはexclusion/focus/payload semantics/comparison/output contract/source-authority/continuationをJSON-safeに表現する。`to_dict()`はJSON transport用のprojectionである。
- 権限: 情報伝達の形式だけを担う。仕様、budget、approval、privacy、Gate、Task state、Schedulerを発行しない。
- 禁止: Control（Directiveを含む）をCompressionへ渡すこと、自然言語rendererをSSOTにすること、compressed payloadを原文の代替にすること、model名をroleやauthorityとして扱うこと、最新訂正と旧解釈を平均・再活性化すること。

`ExternalTextReference`は、再取得可能なPayloadのHTTPS location、SHA-256、size、作成時刻、
任意expiryを検証するmetadata valueである。取得・upload・authorityは所有せず、外部本文は
Controlを上書きしない。`rework_request()`はTask、failure evidence、review finding、required
correctionの差分だけを参照形式で渡す。

### `CompressionService`

- 責務: HandoffのPayloadだけを、固定profileで独立Compression Serviceへ送る薄いHTTP client境界。
- 公開入口: `HttpCompressionService.compress(text, profile="semantic-dense-v1")`、`compress_handoff_payload(...)`。
- 入力/出力: `/v1/compress`へ`text`と固定`profile`だけを送信し、compressed text、digest、文字数、Prompt version、model、warningsを検証して返す。呼出側は原文digestとCompression provenanceをHandoffへ保持する。
- 権限: Compressionのtransportと機械的情報保持検査のみ。Provider routing、任意prompt、tool、budget、Task stateを所有しない。
- 禁止: instruction、conditions、cautions、Directive内authority/source/comparison/output contractの圧縮、compressed payloadを原文SSOTとして扱うこと、未知profileや不一致digestの受理。Service endpointやProviderをHandoff runtimeへ暗黙に接続しない。

### `CodexSupervisedCommanderRun`

- 責務: development-only Commander PlanへSupervisor metadataを保存し、既存のrefresh、dispatch、collect、Host Verification、明示review/integration境界をcompositionする。`advance(...)`は一回のbounded snapshot、`run_until_intervention(...)`は同じrunをWorker terminal／review／安全停止点までblocking継続する。
- 公開入口: `create(...)`、`status()`、`advance(...)`、`run_until_intervention(...)`、`rework_handoff(...)`、`reassign(...)`、`record_review_decision(...)`、`integrate_approved_worker(...)`、互換用`approve_integration(...)`、`python scripts/devfarm_supervisor.py status|resume|run`。
- 入力/出力: existing Commander plan、qualified Worker assignment、compact result/evidence reference、`SupervisorStep`。waitはPlanへ保存される1/5/10/15分のbounded cadence情報であり、LLM busy loopではない。dispatch deadline超過のresult-less taskはorphanとして再送せずreconciliation要求へ送る。review packetはpatch本文ではなくattempt artifact referenceとdigestを返す。
- 権限: 既存Commander、DevFarm、Host Verification、Git-backed integration authorityをcompositionするだけ。Codexの明示承認なしのintegration、Production Scheduler/Task state/Budget authorityの所有、Worker raw conversationの保存を行わない。Codex review decisionはattempt／evidenceへdurably結合するがHuman仕様Authorityへ昇格させない。

### Development handoff roles / one cycle

- 責務: `PlannerRole`、`ExecutorRole`、`ReviewerRole` のmodel-neutralなrole contractと、development-onlyの1回限りのcompositionを提供する。
- 公開入口: `OneCycleDevelopmentLoop.run(...)`、`CodexDevFarmExecutor.execute(...)`。
- 入力/出力: HumanからPlannerへの`analysis_result`、PlannerからExecutorへの`implementation_instruction`、ExecutorからReviewerへの`execution_result`、ReviewerからHumanへのreview/roadmap/decision handoff。
- 権限: 既存DevFarmのmanifest、isolated worktree、Host Verification、attempt artifactをcompositionするだけで、公式branchへのintegration・push・merge・次cycle開始は行わない。
- 禁止: Reviewer出力を自動的に次のPlannerへ送ること、Production Scheduler/Task state/Budget/authorityの二重化、External Workerの自己申告を証拠扱いすること。

## Runtime / intelligence

### `ModelProvider`

- 責務: provider-neutral な一回の推論を実行する。
- 公開入口: `request(ModelRequest) -> ModelResponse`。
- 入力/出力: Kernel の typed protocol のみ。SDK response、function-calling wire schema、provider 固有例外は Adapter 内で正規化する。
- 権限: Provider 通信のみ。Task lifecycle、budget、quota、approval を決めない。
- 禁止: 自動 retry/fallback、Task 状態の直接変更、秘密情報の監査出力。

### `ProviderRegistry` / `ProviderDispatcher`

- `ProviderRegistry`: `provider_binding_id` で具体的な ModelProvider を解決する。`provider_id`（vendor）、`model_id`、binding、resource、credential、quota domain を混同しない。同一 provider の複数 binding を許可する。
- `ProviderDispatcher`: selection、provider intent、resource/budget reservation、fallback、provider audit、reconciliation の canonical 実行境界。timeout後のlate provider completionも同じintent／reservationへreconcileし、新しいProvider requestを発行しない。
- 公開入口: `request(ModelRequest) -> ModelResponse`。Dispatcher は内部で `ResourceControlPlane` と Registry を利用する。
- 権限: Resource/Provider の dispatch authority。ただし Hard Budget、Human Authority、protected config を自己昇格させない。
- 禁止: Controller への provider 固有分岐の追加、Router/ledger の内部実体を `A.B.C` で直接操作すること、UNKNOWN の無条件再送、late completionを別bindingへblind retryすること。

### `ResourceControlPlane`

- 責務: resource selection、health/quota observation、reservation、provider telemetry の facade。
- 入力/出力: provider-neutral な resource/budget/quota 値と durable selection。
- 権限: ResourceLedger と BudgetGovernor の transaction 境界を使用する。
- 禁止: Provider SDK 依存、Task lifecycle の所有、quota reset 時刻の捏造。

### `TaskLifecycleCoordinator` / `FiniteLifecycleLoop`

- 責務: Evaluator の判定を有限の Task transition（PASS、bounded retry、escalation、WAIT_HUMAN、RECONCILING、FAIL）へ接続する。
- 公開入口: coordinator の evaluation/dispatch API、loop の `evaluate_and_apply(...)`。
- 入力/出力: durable Task/Event/transition と typed evaluation evidence。cycle 使用数は `evaluation.recorded` 等の StateStore 履歴から復元する。
- 権限: finite policy に従う lifecycle transition。terminal Task、approval、UNKNOWN effect を勝手に再開・再送しない。
- 禁止: process-memory の counter のみで有限性を保証すること、無限 retry、Model 自身による自己昇格。

### `Controller`

- 責務: Task lifecycle、checkpoint/resume、runtime composition の司令塔。
- 入力/出力: Task と既存の Controller/Dispatcher/Tool 契約。
- 権限: lifecycle を確定するが、canonical Provider 通信は Dispatcher へ委譲する。
- 禁止: 新しい Provider orchestration、Phase 7専用の別 state machine、protected authority の変更。旧 direct-provider path は compatibility のみ。

### `AgentBackend`（thin contract）

- 責務: repo探索、編集、shell、長時間coding loopを持つ外部Agent harnessとの typed handoff を定義する。ModelProviderの一回推論契約とは分離する。
- 公開入口: `AgentBackend.identity`、`start(AgentBackendRequest)`、`events(session_id)`、`cancel(session_id)`、`result(session_id)`。
- 入力/出力: backend identity、task/objective、opaque workspace scope、session、provider-neutral event、completion/failure/unknown/reconciliation result。Backend固有payloadはadapter内に閉じる。
- 権限: 外部Backendへ渡す契約の表現のみ。Task state、Scheduler、Budget、Quota、Approval、Lease/Fencing、Recovery、Gateを所有しない。
- 禁止: ControllerやProviderRegistryの置換、外部Backendへの無承認dispatch、UNKNOWNの再送、Backend固有schemaのKernelへの漏出。実adapterは既存Control Planeの再検証境界を通す。

### `AgentBackendDispatcher`

- 責務: 既存Task、scope、注入されたauthorityを確認した後、effect intentをdispatch identityとして外部AgentBackendのsessionを開始し、Event／result／cancel／explicit reconciliationを既存StateStoreへ接続する。
- 公開入口: `dispatch(request, backend, dispatch_id, attempt)`、`events(dispatch_id, backend)`、`result(dispatch_id, backend)`、`cancel(dispatch_id, backend)`、`reconcile(dispatch_id, backend, actor, source)`。
- 入力/出力: typed `AgentBackendRequest`と外部Backend、durable identity、session、provider-neutral event/result。backend固有IDはopaque sessionとして保持する。
- 権限: dispatch可否の最終authorityは既存Control Planeから束ねた`BackendAdmission`とstrict-`True` authorization callbackに残す。完了済みidentityは冪等に返し、UNKNOWN／RECONCILINGは明示reconcileまで再dispatchしない。
- `BackendAdmission`はtask／dispatch／workspace／scope／sensitivityに結び付いたlease proof、budget admission、approval、allowed capabilitiesの証拠を要求し、`lease_admitted`、`budget_admitted`、`approval_granted`、`privacy_allowed`の各strict-`True` authority flagを必須とする。dispatcherは証拠を発行せず、TaskとBackend identityのrequired capability coverageを開始前に検証する。
- 禁止: ProviderRegistry／ResourceRouterへの登録、新Scheduler／Budget／Approval／Recoveryの所有、公式branchへの直接編集、UNKNOWNのblind retry。

### `ExecutionTargetPolicy`

- 責務: 同じintelligence tier内で、通常の`ModelProvider`実行と明示的に許可された`AgentBackend`実行を分離して選択する小さな判断境界。
- 公開入口: `choose(required_tier, required_autonomy, required_capabilities, ...)`。
- 権限: budget／privacy／approval／capabilityの既存証拠を受け取り、通常は最低限のModelProviderを選ぶ。AgentBackendは明示autonomyと全証拠が揃った場合だけ選択する。
- 禁止: L3というだけでAgentBackendへ自動昇格すること、ProviderRouterの複製、authorityやbudgetの発行。

## Durable state / operation

### `StateStore` (`SQLiteStateStore`)

- 責務: Task、Step、checkpoint、Event、approval、effect intent、reconciliation、provider audit の durable storage と `commit_transition` の transaction ownership。
- 公開入口: `save_task`、`load_task`、event/checkpoint、transition、intent/audit API。
- 権限: 同一 SQLite transaction 内で状態を確定する。
- 禁止: Repository ごとの別 connection で atomicity を壊すこと、Runtime の状態を別メモリDBへ複製すること。
- 内部構造: `state/schema.py`、`state/core_repository.py`、`state/effects_repository.py`が責務別実装を担い、`state/views.py`の`TaskStateView`、`EventStore`、`EffectIntentStore`、`ProviderAuditStore`はcomponent向けのnarrow viewを提供する。SQLiteStateStore facadeとtransaction ownerは分割しない。leaseの共有fencing primitiveは`persistence/lease.py`に置く。

### `DurableQueue`

- 責務: Task の enqueue、claim、lease、defer/wake、bounded retry、cancel。
- 入力/出力: Task ID と durable queue item。lease/fencing を検証可能な形で扱う。`claim_count`／`claim_streak`（lease claim）と`execution_attempts`（実行開始）は別カウンタで返す。
- 権限: scheduler/worker の queue ownership。
- 禁止: Task の正本状態を queue 内だけに持つこと、expired lease の無条件実行。

### `Operation Layer`

- 公開入口: `dev-agent start|submit|status|stop`、`OperationService`。
- 責務: 人間の操作を既存 StateStore、Queue、WorkerRunner、Controller、Dispatcher、Evaluator/Lifecycle へ composition する。`maintenance_tick` は既存 quota wake / one-shot requalification と、durable provider intentがlate completionで確定したTaskのbounded wakeを接続する。
- Lifecycle composition: `OperationService.evaluate_task(...)` は既存 `FiniteLifecycleLoop`、`EvaluationCoordinator`、`TaskLifecycleCoordinator` を使って host evidence を一回の有限 cycleへ接続する。`review_task(...)` は明示 reviewだけを記録し、`dispatch_reviewed(...)` は queue の lease proof を必須として既存 `EscalationExecutor`／`ProviderDispatcher`へ委譲する。Higher-tier dispatchは既存のexplicit review境界を越えない。
- Planning composition: `validate_planning_proposal(...)` は root Task、capability vocabulary、TaskGraph上限、dependency cycle、protected owner、sensitivity monotonicityをHost側で検証する。`apply_planning_proposal(...)` は検証済みproposalだけを既存TaskGraph／StateStore／Queueへ反映し、依存childは`WAITING_DEPENDENCY`へdurably parkする。Planner出力はbudget、approval、privacy、Gate authorityを発行しない。
- 入力/出力: task objective、Task ID、durable status JSON。CLI 独自の Task 状態を持たない。
- 権限: 起動/投入/停止の operator boundary。stop は未知の外部効果を FAILED に偽装しない。
- 禁止: 新しい scheduler/state machine、busy polling、budget/quota/approval の bypass。
- Resource 起動規則: 既存 Resource catalog、価格、quota domain、health、operator metadata は read-only で扱う。free 判定は trusted な binding×model catalog に限定し、未知価格は推測せず拒否する。cloud Resource の quota domain は operator-owned 設定として明示され、正常な Provider 応答または bounded probe だけが freshness を更新する。
- Capability／Task入力規則: QualificationResolverがcurrent qualification evidenceからcanonical execution capabilityを導出する。`architecture`等のTask competency／policy traitはtier・risk・approvalへ使うがRouterのProvider capabilityへ渡さず、`submit`／TaskIntelligencePolicy入口では未知capabilityをfail-fastする。期限切れ・未資格化のmodel名やtier推測はproduction routing authorityにならない。
- Waiting／wake規則: `resource:provider_execution_saturated:<binding>`は該当binding laneのcapacity/reconciliation boundaryだけがwakeし、quotaは`quota:<domain>`、reconciliationはdurable effect outcome、approval／budget／maintenanceは各authorityがwakeする。waitingはclock経過やqueue claimだけで再実行可能にならず、claim counterとlogical execution attemptを分離する。
- 拒否/停止規則: `DispatchDenied` は budget、quota、maintenance、resource wait、invalid failure の意味を保持して Task 状態へ写像する。cross-process cancellation は durable control と terminal commit 時の再確認を通り、外部効果不明時は `WAITING_RECONCILIATION` を維持する。late provider successは保存済み応答のlifecycle replayへ戻し、blind retryしない。
- Provider composition: 通常運用は複数のqualified bindingを`ProviderFactory`／`ProviderRegistry`へ登録でき、exact current intelligence tierをhard filterしたうえで同Tierの別bindingへbounded fallbackする。単一provider指定はdebug／qualification／manual pinとして扱う。
- Configured cloud bindings: `gemini`はcredential environment nameとproject-scoped quota domainをbinding identityへ分離して複数keyを扱う。`ollama_cloud`（`OLLAMA_API_KEY`）と`vercel`（`AI_GATEWAY_API_KEY`）はそれぞれ独立したOpenAI-compatible adapter identityであり、local `ollama`とは別物とする。API keyの存在はqualification／billing admissionの証拠ではない。allowance-backed billingは`free_fixed`と区別し、exact model profileが無い場合はproduction routingへ投影しない。
- 内部composition: `operation_bootstrap.py`はprovider／qualification／resource／budgetの構成、`operation_planning.py`はplanning contextと依存childの検証・適用、`cli.py`はargparse入口、`state/control_repository.py`はdurable stop controlを所有する。`operation.py`は公開facadeとしてこれらをcompositionし、外部のstart／submit／status／stop意味は変更しない。

## Development-only DevFarm / Commander

### `WorkerRunner` / `DevFarm`

- `WorkerRunner`: 固定 manifest の Task を isolated worktree で実行する既存 worker 境界。
- `DevFarm`: Remote Proposal と Host Verification を分離し、outbound file、provider allowlist、patch path、secret、protected path、test command を検証する。
- 入力: manifest の base revision、read/outbound scope、allowed/forbidden files、acceptance、bounded test command。
- 出力: proposal、validated patch、host-verified result、metrics artifact。
- retry artifact: proposal/result は attempt identity ごとの immutable artifact を正本とし、root の latest projection を検証対象へフォールバックしない。
- 権限: 指定 worktree/proposal artifact のみ。公式 branch への自動 apply/merge はしない。
- Host Verificationは`STATIC_ONLY`を外部Providerの既定とし、patch/path/Git適用性とimmutable evidenceだけを検証してpatched codeを実行しない。`TRUSTED_HOST_EXEC`はattempt単位のoperator approvalが必要で、`OS_SANDBOXED`はfilesystem/network/resource isolationが実装されるまで利用不可。現行のsanitized environment、temporary HOME、bounded/secret-sanitized output、timeout時process-tree終了はOS sandboxではない。
- Host test commandはpytest/compileall、相対worktree target、`-q`／`-x`／bounded `--maxfail=N`だけを許可する。Worker変更ファイルだけを対象にしたtestは正式acceptance evidenceにならず、unmodified trusted targetも必要とする。attemptのpatch、manifest、test spec digestとverification recordをGit integration proofへ結合する。
- 禁止: root fallback、未承認送信、worker 間直接通信、Model 自己申告 test の証拠化。

### `CommanderPlanStore` / Commander

- 責務: development-only の親 Plan、Task DAG、ownership、assignment、result reference、retry/reassign、integration 記録を durable に管理する。
- 公開入口: `devfarm plan|dispatch|status|collect|verify|resume|reassign|mark-integrated`。
- 入力/出力: Plan/Task manifest と `.devfarm/plans/` の status/result。Worker成果は Host Verification 前に正式成果とみなさない。
- 権限: Codex が decomposition、review、integration を行う。Protected authority と Production scheduler/state を変更しない。
- 禁止: Worker の自動 merge、重複 ownership、`HOST_VERIFIED` だけで code dependency を release、Production Runtime の Multi-Agent framework 化。

### Resource qualification / repair

- `QualificationResolver.resolve(provider_id, provider_binding_id, model_id)` は Production
  routing projectionであり、exact current identityかつ`high` confidenceだけを返す。
  low/medium confidenceの観測は`resolve_observed(...)`で監査・repair用途に参照できるが、
  Routerのrouting grantにはならない。
- `dev-agent resource validate` は旧Resourceを非破壊で報告する。`resource migrate`は
  dry-runが既定で、`--apply --operator-ref`を伴う明示操作だけがResource catalogを更新し、
  schema-v10の`resource_repairs`へbefore/afterを監査保存する。通常Operation起動はrepairを行わない。

### Planner dependency release

- `ChildTaskProposal.dependency_types` は依存keyごとの`ARTIFACT_READY`、`TASK_COMPLETED`、
  `CODE_INTEGRATED`を表す。旧`dependencies`だけの入力は`TASK_COMPLETED`として扱う。
- `CODE_INTEGRATED`は依存Taskの`integration_status=INTEGRATED`と非空`integration_revision`を
  必須とし、Task完了だけでは後続Taskをreleaseしない。Planner出力は証拠を発行せず、Host側で
  検証されたdurable metadataだけがrelease authorityになる。

### Provider execution saturation wake

- binding単位のsaturationは`resource:provider_execution_saturated:<binding>`へparkする。
  一つのdispatch cycleで全eligible bindingがsaturatedになった場合は
  `resource:provider_execution_saturated:pool`へparkし、任意のlane capacity recovery通知で
  一回だけwakeして再選択する。busy polling、無関係waitのwake、外部結果不明のblind retryは禁止する。
