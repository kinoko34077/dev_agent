# 内部 API 仕様（Phase 7 統合境界）

この文書は、現行 v2 の公開境界を定義する正式契約である。開発者向けの配置索引は `docs/SYSTEM_MAP.md`、現在の証跡は `docs/CURRENT_STATE.md` が担う。ここに記載のない内部実装は公開契約ではない。

## 共通原則

- Provider SDK 型、HTTP payload、Agent/OSS 固有型はこの境界へ漏らさない。
- 外部副作用は durable effect intent、budget、approval、lease/fencing、audit を通過する。結果不明時は `UNKNOWN` / reconciliation を維持する。
- API が実装済みであることだけでは Gate の VERIFIED を意味しない。
- `StateStore`、Queue、Resource、Recovery の正本を二重化する実行系を作らない。

## Runtime / intelligence

### `ModelProvider`

- 責務: provider-neutral な一回の推論を実行する。
- 公開入口: `request(ModelRequest) -> ModelResponse`。
- 入力/出力: Kernel の typed protocol のみ。SDK response、function-calling wire schema、provider 固有例外は Adapter 内で正規化する。
- 権限: Provider 通信のみ。Task lifecycle、budget、quota、approval を決めない。
- 禁止: 自動 retry/fallback、Task 状態の直接変更、秘密情報の監査出力。

### `ProviderRegistry` / `ProviderDispatcher`

- `ProviderRegistry`: `provider_binding_id` で具体的な ModelProvider を解決する。`provider_id`（vendor）、`model_id`、binding、resource、credential、quota domain を混同しない。同一 provider の複数 binding を許可する。
- `ProviderDispatcher`: selection、provider intent、resource/budget reservation、fallback、provider audit、reconciliation の canonical 実行境界。
- 公開入口: `request(ModelRequest) -> ModelResponse`。Dispatcher は内部で `ResourceControlPlane` と Registry を利用する。
- 権限: Resource/Provider の dispatch authority。ただし Hard Budget、Human Authority、protected config を自己昇格させない。
- 禁止: Controller への provider 固有分岐の追加、Router/ledger の内部実体を `A.B.C` で直接操作すること、UNKNOWN の無条件再送。

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
- 権限: dispatch可否の最終authorityは注入された既存Control Plane callbackに残す。完了済みidentityは冪等に返し、UNKNOWN／RECONCILINGは明示reconcileまで再dispatchしない。
- 禁止: ProviderRegistry／ResourceRouterへの登録、新Scheduler／Budget／Approval／Recoveryの所有、公式branchへの直接編集、UNKNOWNのblind retry。

## Durable state / operation

### `StateStore` (`SQLiteStateStore`)

- 責務: Task、Step、checkpoint、Event、approval、effect intent、reconciliation、provider audit の durable storage と `commit_transition` の transaction ownership。
- 公開入口: `save_task`、`load_task`、event/checkpoint、transition、intent/audit API。
- 権限: 同一 SQLite transaction 内で状態を確定する。
- 禁止: Repository ごとの別 connection で atomicity を壊すこと、Runtime の状態を別メモリDBへ複製すること。

### `DurableQueue`

- 責務: Task の enqueue、claim、lease、defer/wake、bounded retry、cancel。
- 入力/出力: Task ID と durable queue item。lease/fencing を検証可能な形で扱う。
- 権限: scheduler/worker の queue ownership。
- 禁止: Task の正本状態を queue 内だけに持つこと、expired lease の無条件実行。

### `Operation Layer`

- 公開入口: `dev-agent start|submit|status|stop`、`OperationService`。
- 責務: 人間の操作を既存 StateStore、Queue、WorkerRunner、Controller、Dispatcher、Evaluator/Lifecycle へ composition する。`maintenance_tick` は既存 quota wake / one-shot requalification を bounded に接続する。
- 入力/出力: task objective、Task ID、durable status JSON。CLI 独自の Task 状態を持たない。
- 権限: 起動/投入/停止の operator boundary。stop は未知の外部効果を FAILED に偽装しない。
- 禁止: 新しい scheduler/state machine、busy polling、budget/quota/approval の bypass。

## Development-only DevFarm / Commander

### `WorkerRunner` / `DevFarm`

- `WorkerRunner`: 固定 manifest の Task を isolated worktree で実行する既存 worker 境界。
- `DevFarm`: Remote Proposal と Host Verification を分離し、outbound file、provider allowlist、patch path、secret、protected path、test command を検証する。
- 入力: manifest の base revision、read/outbound scope、allowed/forbidden files、acceptance、bounded test command。
- 出力: proposal、validated patch、host-verified result、metrics artifact。
- 権限: 指定 worktree/proposal artifact のみ。公式 branch への自動 apply/merge はしない。
- 禁止: root fallback、未承認送信、worker 間直接通信、Model 自己申告 test の証拠化。

### `CommanderPlanStore` / Commander

- 責務: development-only の親 Plan、Task DAG、ownership、assignment、result reference、retry/reassign、integration 記録を durable に管理する。
- 公開入口: `devfarm plan|dispatch|status|collect|verify|resume|reassign|mark-integrated`。
- 入力/出力: Plan/Task manifest と `.devfarm/plans/` の status/result。Worker成果は Host Verification 前に正式成果とみなさない。
- 権限: Codex が decomposition、review、integration を行う。Protected authority と Production scheduler/state を変更しない。
- 禁止: Worker の自動 merge、重複 ownership、`HOST_VERIFIED` だけで code dependency を release、Production Runtime の Multi-Agent framework 化。
