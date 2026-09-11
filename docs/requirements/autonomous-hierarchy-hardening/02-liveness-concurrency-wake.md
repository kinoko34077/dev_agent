# 02 Liveness / Concurrency / Wake

対象: B1–B4、C1–C3、待機理由とwake authority、Provider別concurrency

## B1/B2 — Provider execution saturation

ModelTurnExecutorのtimeout後Future追跡と、無制限orphan threadを防ぐ上限は維持する。
ただし、1つのProvider bindingのhangをDispatcher全体またはProvider pool全体の停止へ
波及させない。

目標境界:

```text
ResourceRouter
  → selected provider binding
  → per-binding ProviderCallExecutor
  → concrete Provider
```

各bindingまたは明示されたexecution laneごとに、timeout、orphan、saturation、circuitを
独立管理する。Gemini bindingがsaturatedでもCloudflare / OpenRouterは選択可能とする。
ただし同じRequestの外部結果がUNKNOWNなら、別bindingへblind retryしない。binding障害と
Request outcomeの不確実性を別状態で保持する。

## Saturation waitのwake

`ProviderExecutionSaturated`を単なるqueue deferで終わらせない。次のdurable wait reasonを
利用する。

```text
resource:provider_execution_saturated:<provider_binding_id-or-lane>
```

複数のeligible laneを一つのdispatch cycleで使い切った場合は、pool wait identity
`resource:provider_execution_saturated:pool`を使用する。いずれか一つのlaneが復帰した
通知でpool waitを一度wakeし、WorkerRunnerがResourceRouterで再選択する。単一laneの
waitは従来どおりbinding単位に限定し、quota／approval等の無関係なwaitはwakeしない。

capacityが解放されたことを、ModelTurnExecutorのcompletion通知または既存Operation /
Scheduler maintenance boundaryからboundedにQueueへ伝える。

```text
orphan resolved / process restart reconciliation
  → lane capacity available
  → matching wait reasonだけをbounded wake
```

busy pollingは禁止する。Process restart時はprocess-local orphanを0から再構築し、古い
saturation waitを永続状態から安全にwakeできること。

## B3 — claimとlogical retryの分離

Queueのlease/claim回数とTaskのlogical execution budgetを同じカウンタとして扱わない。
最低限、次を意味分離する。

- `lease_claim_count`: schedulerがclaimした回数。crash-loop防止用に維持する
- `execution_attempt_count`: Provider/Tool実行を実際に開始した回数
- `model_attempt_count`: Model turnを開始した回数
- `escalation_count`: tier escalationを消費した回数

quota、budget、approval、saturation、maintenance待ちなど、外部実行前にparkしただけの
遷移はlogical execution retry budgetを消費しない。既存のlease expiry / crash-loop
上限は削除しない。

## B4 — late completion

実装状態: `ProviderDispatcher`、`ModelTurnExecutor`、`Controller`、`Operation.maintenance_tick()`へ接続済み。focused testは
`test_late_provider_completion_reconciles_once_and_resumes_task`。

外側ControllerがtimeoutしてTaskをWAITING_RECONCILIATIONへ送った後、裏のProvider callが
late completionする場合を明示的に扱う。

```text
late completion
  → durable provider intentを正本として再評価
  → succeeded / confirmed_failedなら新Requestを送らずlifecycle continuation
  → unknownならWAITING_RECONCILIATIONを維持
```

late responseを理由にblind retryしない。effect intent、budget reconciliation、Task /
Queue wakeが矛盾しないことを確認する。

## C1 — race-path error

AgentBackend race pathで未定義名をraiseしてはならない。`BackendDispatchUncertain`等の
既存typed errorへ統一し、NameErrorを発生させない。raceのwinner/loserはdeterministicな
replayまたはreconciliation状態を返す。

## C2 — AgentBackend atomic dispatch claim

同一 `dispatch_id` の外部 `backend.start()` は一度だけ許可する。既存effect intentへ
expected-state付きatomic claim / fencingを追加し、SELECT後の無条件UPDATEだけで済ませない。

```text
pending / prepared
  → atomic claim
  → dispatching(owner token)
  → winnerだけbackend.start()
```

競合loserはdurable stateを再読込する。

- sessionが保存済み: 同じsessionを返す
- dispatchingかつsession不明: `BackendDispatchUncertain` / reconciliation
- terminal: durable resultをreplay
- unknown: reconciliationへ維持

`create_effect_intent` race、pending/prepared race、session persist直前crashを個別に
注入し、`backend.start_calls == 1`を確認する。

## C3 — Event polling dedupe

同一Backend sessionを複数callerがpollしても、同じsequenceが重複保存されないことを
確認する。再現した場合は、既存Event schema / transaction ownerを使って
`(dispatch_id, sequence)`相当のdurable uniquenessまたはatomic insertを追加する。
新しいEvent storeやSchedulerは作らない。

## Waiting contract

全waiting stateは、理由・wake authority・wake condition・restart behavior・retry budget
への影響を持つ。

| wait reason | wake authority | wake condition | restart / retry |
| --- | --- | --- | --- |
| `quota:<domain>` | QuotaRequalificationCoordinator | reset到来後のbounded probe成功 | blocked_untilを保持、probe失敗でblind retryしない |
| `resource:provider_execution_saturated:<binding>` | binding capacity / reconciliation boundary | lane capacity解放 | claimとlogical retryを分離 |
| `maintenance` | maintenance authority | maintenance解除 | execution budgetを消費しない |
| `resource:no_route` | resource / qualification / config authority | eligible route出現 | status変化までbusy pollingしない |
| `resource:unavailable` | health observation authority | fresh healthy observation | stale observationを成功扱いしない |
| `approval` | Human / Approval authority | explicit approval | approval bypass不可 |
| `budget` | BudgetAuthority / period rollover | reserve可能なperiod | hard cap変更権限をRuntimeへ渡さない |
| `reconciliation` | explicit Reconciliation authority | 外部outcome確定 | UNKNOWNを自動再送しない |

## 受入テスト

- Gemini binding hang → Gemini laneだけsaturated、Cloudflareは選択可能
- orphan completion → saturation waitがmatching bindingだけwake
- process restart → stale saturation waitをcapacity再確認後にwake
- saturation待ち → logical retry budgetを消費しない
- late provider success →新しいProvider requestなしでdurable lifecycleを継続
- 同一AgentBackend dispatchを2 caller / 2 StateStore connectionで競合 → start一回
- race loser、session persist直前crash、unknownをtyped error / reconciliationへ導く
- 同一Backend event sequenceのconcurrent pollを重複保存しない
