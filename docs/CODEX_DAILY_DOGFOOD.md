# Codex Daily Dogfood Runbook

このRunbookは、既存のCommander・Supervisor・DevFarm・Free Workerを、通常の開発で
繰り返し使うための短い操作手順である。新しいScheduler、Task state machine、retry
frameworkは追加しない。Local Operation runtime coordinatorは既存Operation loopを
foregroundで生かす薄いprocess boundaryであり、Task scheduling authorityを持つdaemon
ではない。Compression Serviceは固定payload最適化として明示的に
利用できるが、G6O1のbilling/evidenceへは接続しない。詳細な契約は
[`CODEX_COMMANDER.md`](CODEX_COMMANDER.md)、[`CODEX_SUPERVISOR.md`](CODEX_SUPERVISOR.md)、
[`DEVFARM.md`](DEVFARM.md)を正本とする。

## 開始

Humanの通常指示は、例えば次の短文でよい。

```text
dev_agentの日常運用で、現行ロードマップの次を進めて。
```

対象や除外が必要な場合だけ追加する。

```text
    dev_agentの日常運用で、Group Dの次の狭いTaskを進めて。
ただしG6O1には触らない。
```

Codexは次の順でpreflightし、決定可能なファイル・テスト・WorkerをHumanへ聞き返さない。

1. branch / HEAD / dirty state
2. `docs/CURRENT_STATE.md`
3. `docs/V2_EXECUTION_PLAN.md`
4. relevant requirements/spec
5. Gateと凍結・外部blocker
6. `.devfarm/plans/`のunfinished Plan

未完了Planが同じ目的なら新Planを作らず、`status`で確認してから`resume`または`run`
で再開する。Providerのunknown external effectを再送しない。

## Local Operation runtime coordinator

Codexのturnが終了しても、既存のdurable Operation stateをforeground processで軽く
維持・再開したい場合は、薄いCoordinatorを使う。Coordinatorはpeer heartbeat、
generation fencing、idle waitだけを担当し、Task claim、dependency/quota/reconciliation
wake、Provider routing、retryは既存Operationへ委譲する。待機中のLLM呼出しやProvider再送は
行わない。

状態照会:

```text
python scripts/devfarm_runtime_coordinator.py health --data-dir .dev_agent
```

安全なbounded smoke:

```text
python scripts/devfarm_runtime_coordinator.py serve --data-dir .dev_agent \
  --provider fake --model deterministic --max-cycles 2
```

通常のforeground運用では`serve`から`--max-cycles`と`--max-runtime-seconds`を外す。
OS startup、Task Scheduler/Windows Service登録、配備後Guardian livenessは別のProduction
Deployment trackであり、このCoordinatorのlocal Evidenceからは昇格しない。

## PlanとWorker

ロードマップの次項目を、単一責務・非重複ownership・明確なacceptanceを持つTaskへ分解する。
Worker候補は狭い実装、focused test、fixture、parser、serializer、docs、mechanical
refactorである。architecture、security、authority、budget、recovery、protected path、
cross-cutting conflict、最終reviewとintegration判断はCodex担当として理由を記録する。

既存入口でPlanを作成する。

```text
python scripts/devfarm.py plan .devfarm/plan-input.json --root .
```

Plan作成後、通常のWorker待機はblocking `run`を使う。

```text
python scripts/devfarm_supervisor.py run <run-id> --root . \
  --trust-level TRUSTED_HOST_EXEC --operator-approved
```

既定trust levelは`STATIC_ONLY`のままにする。上記のtrust levelとoperator approvalは、
外部生成patchをそのattemptでHost Verificationする、明示的な開発dogfood操作に限る。
Worker作業中はCodexがstatusを短周期pollしたり、同じ実装を並行生成したりしない。

## Review / integration

Free L2 Reviewer Shadow is proposal-only, so Codex final review and Host deterministic integration remain required.

`run`が返したReviewPacketを最初に読む。通常見るのはtask・attempt・provider/model・
changed files・patch digest・verification summary・known issues・acceptance・artifact
referenceだけで、raw Worker会話や巨大patchは必要時にだけ取得する。

承認する場合:

```text
python scripts/devfarm_supervisor.py review <run-id> <task-id> --root . \
  --attempt-id <attempt-id> --decision APPROVE_INTEGRATION \
  --evidence-ref <verification-artifact>
python scripts/devfarm_supervisor.py integrate <run-id> <task-id> --root . \
  --decision-id <decision-id> --target-checkout . \
  --target-ref HEAD --commit-message "<message>"
```

Gitの適用・check・commit・`mark_integrated`はHost helperに任せ、Codexが毎回手作業で
行わない。push、merge、Production deployはこのRunbookの自動操作に含めない。

修正が必要な場合:

```text
python scripts/devfarm_supervisor.py review <run-id> <task-id> --root . \
  --attempt-id <attempt-id> --decision REWORK \
  --required-correction "<短い修正内容>"
python scripts/devfarm_supervisor.py rework <run-id> <task-id> --root . \
  --failure-evidence-ref <failure-artifact> \
  --required-correction "<同じdurable correction>"
```

`rework`は元manifestを上書きせず、failure evidence・review findings・required correction
だけの差分Handoffとimmutableな新manifestへ接続する。attempt上限を超える再試行、
unknown external effectのblind retry、Host Verificationの緩和は禁止する。

## 継続・再開・checkpoint

通常のWorker完了、単一Taskのintegration、heartbeatだけではHumanへ戻らない。同じ
roadmap sliceに安全に進められるREADY Taskがあれば、dependency release後に次Taskへ進む。
Humanへ返すのは、仕様・優先順位・budget・security/privacy・protected authorityの判断、
meaningful checkpoint、terminal blocker、slice完了である。初期rolloutのcheckpointは
1 sliceまたは最大3 integrated Worker tasks程度に抑える。

processやCodex会話が途切れた場合は、次で状態を復元する。

```text
python scripts/devfarm_supervisor.py status <run-id> --root .
python scripts/devfarm_supervisor.py resume <run-id> --root .
```

`DISPATCHED`のorphanは既存deadline/reconciliation境界で扱う。`status`や`resume`は
未知の外部効果を勝手に再実行しない。`REVIEWING`、`INTEGRATING`、`REWORK`では表示された
Codex actionを先に処理し、unfinished Planを閉じるまで同じ目的の新Planを作らない。

作業中の表示位置は既存Task UUIDに任意の`work_address`を付けて管理する。形式と
割込みの扱いは[`CODEX_WORK_COORDINATION.md`](CODEX_WORK_COORDINATION.md)を正本とし、
曖昧な横槍は現在作業を止めないNOTE/PARALLELへ倒す。checkpointはResume Capsuleと
immutable artifact referenceで残し、アドレスだけから依存やownershipを推測しない。
Commander Planでアドレスを省略したTaskはHostがroot位置から自動発番できる。
親付きの割込み・枝は`work_address_parent`と`work_address_kind`で明示する。作業中
ファイルの重複確認は次の読み取り専用照会を使う。

```text
python scripts/devfarm_supervisor.py ownership <run-id> --root .
python scripts/devfarm_supervisor.py ownership <run-id> --root . --all
```

これは既存Commanderのownershipを表示するだけで、claim/releaseやTask状態変更は行わない。

外部Workerへ送るファイルは、既存manifestの明示scopeに加え、Host側のstanding egress
grantとdispatchごとのcontent scan/hashを通す。ALLOW以外は送信せず、secret・credential・
protected source・raw conversationは送信・保存しない。

## 対象外と終了報告

Humanが明示的に再開しない限り、G6O1-SIM/LIVE、real paid-provider qualification、
OpenAI/Claude API、OS-level sandbox、Discord/UI、Phase 9 UIは
日常Task候補へ入れない。G6O1は`DEFERRED_FROZEN`・`NOT VERIFIED`・現行roadmap
non-blockingのまま保持する。Compressionは明示的な固定client composition時だけ使い、
3,000 Unicode code points超のPayloadを対象にする。Controlは圧縮せず、失敗時は
bounded fallbackまたはfail-closedとし、G6O1を進めた証拠にはしない。

slice終了時は、次だけをcompactに報告する。

```text
完了: <Task / slice>
Worker: <件数> / REWORK: <有無>
Delegation: Worker tasks <件数> / Codex tasks <件数> / Codex direct <件数> (<理由>)
Integration: <主要commit>
検証: <focused / full / architecture / compileall>
CI: <exact-headの有無>
残件: <次Taskまたはblocker>
Human判断: <要/不要>
```

GitHub branch protectionのrequired checksは外部設定であり、未設定なら未設定と記録する。
コードやartifactだけで完了扱いにしない。
