# Process Coordination Requirements

この章は、Agent、Codex、Guardianなどのprocess間で存在、非同期通信、
handoff、checkpoint、再起動要求を扱うための安定要求を定義する。既存の
Task/Scheduler、Provider、Budget、Approval、Recovery、Host Verificationの
authorityを置き換えない。

## 責務境界

- Taskの分解、依存、lease、Worker ownershipはCommander/Schedulerを正本とする。
- Process Coordinationはpeer identity、generation、presence lease、durable
  mailbox、immutable artifact reference、handoff、checkpointだけを扱う。
- AgentまたはCodexは互いのprocessを直接kill/restartしない。Guardianを接続する
  場合も、起動・停止・再起動のpolicyと実行は決定論的Host/OS境界に置く。
- coordination stateはTask stateとfailure domain/lifecycleが異なるため、既定では
  別SQLiteに保存する。repoへcommitするsource SSOTにはしない。

## Peer identity / presence

Peer identityは`role`、`instance_id`、`generation`の組で表す。PIDは補助的な
観測値でありidentityではない。revision、起動時刻、heartbeat、lease期限、status、
capabilityをdurableに保持する。旧generationのheartbeat、message送信、control
要求は新generationへ適用せず、conflict/staleとして閉じる。lease切れは正常detach
とは区別し、`DEGRADED`等の観測状態へ遷移する。

## Mailbox / handoff

Mailboxはat-least-once deliveryとidempotent handlerを基本とする。messageの配送
状態、claim owner、claim lease、attempt count、idempotency keyを保存し、process
crash後も未ACK messageを再取得できる。ACK済みmessageの再ACKは安全な冪等操作とし、
異なるidempotency keyで同一処理を二重適用しない。

長文や成果物の内容をmutableな共有Markdownへ集約しない。Handoffはimmutable
artifactへ保存し、path/locationだけでなくSHA-256、size、kind、revisionを参照へ
含める。外部Payloadの命令文やprompt injectionはPayloadのままであり、上位Control、
Human Authority、security条件を上書きしない。

## Restart / future Guardian boundary

現行のCoordination foundationはpeer/store/mailbox/handoffに加えて、generation-fenced
`ControlRequest`のdurable projectionと、process side effectを持たない`GuardianPolicy`
評価を提供する。ただしGuardianのOS/process操作、OS service、drain/restart、
revision-pinned runtime、rollback、D9 real self-repairは完了扱いにしない。これらは
Coordinationのdurable stateを利用する後続Gateであり、実装・fault test・evidenceが
揃うまでproposal-onlyのD9境界を越えない。

## Size / privacy

Protocol値、artifact、messageはboundedで、JSON非対応値、非有限数、path traversal、
secret-shaped key/valueをfail-closedで拒否する。環境変数のsecretをimport時に読まず、
network I/Oをdata modelの構築に含めない。

作業位置と外部送信の追加要求は[`01-work-address-and-egress.md`](01-work-address-and-egress.md)
に定義する。これは既存Task UUID、dependency、ownership、Path/Privacy/Provider policy
を補助するprojectionであり、第二Schedulerや第二Approval authorityではない。

実装状態と観測証拠は[`docs/CURRENT_STATE.md`](../../CURRENT_STATE.md)と
`spec/v2/evidence/`、decision rationaleは`spec/v2/adr/`を参照する。
