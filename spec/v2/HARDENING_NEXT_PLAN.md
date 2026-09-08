# Hardening next plan (completed / frozen)

目的は、外部サービスが停止してもローカルで検証可能な境界を先に完了し、外部依存の項目を明確な保留として後回しにできる状態を作ること。

Current State sync: Phase 3.5〜5 acceptance remains closed; the current Phase 6
code evidence baseline is `88c08df`. Phase 6 is documented separately in `docs/PHASE6_PLAN.md`; this document is the
historical hardening record. Gate evidence distinguishes local, CI, and live
Provider evidence.

## 現在の実行範囲（2026-09-08 JST）

追加された v2 roadmap / foundation 要件に合わせた Phase 3.5
（Kernel Trust Boundary Closure）の完了記録である。Phase 3.5 / 4 / 5 の
current acceptance は `VERIFIED` となり、Phase 6 の実装は
`docs/PHASE6_PLAN.md`へ移管した。

直近の実装順は次の通り。

1. Controller の critical transition、承認、照合、キャンセル境界の fault matrix を拡張する。
2. Tool の実効引数を operation identity・approval・audit・dispatch で共通化する。
3. Event の secret classification と artifact / retention 境界を完成させる。
4. trusted in-process、subprocess、外部ネットワークの timeout / cancellation 契約を個別に検証する。
5. ローカル Provider の real Tool-call E2E と、外部 Provider の BLOCKED 条件を再確認する。

Phase 6 着手条件は満たされ、`GATE_STATUS.json` の `phase6_entry` は
`ALL_VERIFIED` である。

## Gate A — durable state / recovery（先行）

合格条件:

- 正常・承認待ち・失敗・完了の SQLite 状態を独立 validator が読める。
- 必須テーブル、task payload、checkpoint state、approval scope の破損を検出する。
- validator は通常 Runtime / Provider を import せず、ネットワークなしで実行できる。

失敗時の扱い: 状態を修復・上書きせず `FAIL` と診断し、バックアップからの再検査へ回す。

進捗: 必須テーブル・schema version・task payload・approval・effect intent・provider dispatch audit に加え、ResourceLedger の schema v4 と Budget intent binding、orphan Step / checkpoint、壊れた ToolResult、未知の intent status を独立 validator で検出する。Gate A のローカル検査条件は達成。

## Gate B — crash / resume matrix（ローカル完結）

合格条件:

- pre-model、pending tools、各 ToolResult、after-tools、after-model、failure の各永続化境界で停止を注入する。
- 再開時に副作用 handler と provider request の重複回数を観測し、期待値と一致する。
- 期待値を保証できない外部副作用は `at-least-once` と明記し、外部 idempotency / reconciliation がない限り昇格しない。

## Gate C — external side-effect semantics（設計＋ローカル試験）

合格条件:

- outbox intent、外部 request key、結果、reconciliation 状態を永続化する。
- 「外部成功後、local result 保存前の停止」を `unknown` として再実行せず、照合へ回す。
- 決済・公開などの exactly-once を SQLite 単体で主張しない。

外部 API が必要な試験は mock provider で先行し、live 試験は credential / quota 待ちとして保留できる。

進捗: SQLite / JSON の effect intent と、全ローカル precondition 後の作成、原子的 claim、外部処理後のローカル保存前停止を `reconciliation_required` として再実行禁止にする基礎契約を実装済み。timeout、connection failure、response decode、result byte limit、output schema failure も同じ契約へ統一した。曖昧な外部状態は Task の `waiting_reconciliation` として永続化し、照合で intent を succeeded に確定した後に安全再開できる。Controller は active model request ID を checkpoint へ保存し、Provider dispatch では Budget reservation も intent key に永続結合するため、budget 状態だけ保存されたクラッシュ後に新しい外部 request / 二重予約を作らず再利用する。実外部APIの照合アダプタ自体は未実装。

Execution progress: task wall-clock deadline is now persisted in checkpoint state and survives resume; expired resumed work fails closed. Input token estimates, provider-reported output/cost usage, and TaskGraph limits are enforced. Trusted in-process handlers retain a documented soft timeout; untrusted/generated/process handlers use a subprocess boundary with process-tree termination. Scheduler retries are now finite: WorkerRunner derives total attempts from `max_retries + 1`, and DurableQueue applies a default cap when callers omit a limit.

Cancellation progress: `Controller.cancel()` is cooperative and durable at the next runtime boundary. In-flight trusted Python handlers and Provider request threads cannot be force-killed; both now persist `unable_to_confirm` and remain reconciliation-gated, while cancellation before execution persists `terminated`.

Event security progress: oversized sanitized payloads can be written through an
explicit `EventArtifactStore` with content-addressed references, root-bound reads,
secret-pattern rejection, and expiry purge. `recovery/backup.py` now validates and
atomically copies the artifact root for RecoveryOperator backup/restore; operators
still choose the production retention window and broader secret classification.

Provider contract progress: the offline harness now exercises model-generated
ToolCalls, sequential ToolCalls, normalized ToolResults, and final response
roundtrip. Live Gemini and Ollama qualification now have observed capability
matrix entries; Ollama `qwen3:8b` emits a possible `<think>` trace that remains
an explicit output-quality quirk.

Recovery progress: `recovery/validate_artifacts.py` and
`recovery/diagnose.py --artifact-root` independently validate event artifact
digests, metadata, byte lengths, and missing payloads. The artifact root remains
an explicit operator backup/retention input rather than an implicit database
sidecar.

Phase 6 recovery progress: RuntimeState, StateStore, ResourceLedger, and DurableQueue now use ordered schema
migrations. The RecoveryOperator drill exercises validated SQLite restore, LKG
recording, rollback, and repair-branch creation in an isolated temporary Git
checkout; production mutations remain explicit operator actions.

Approval progress: approval records bind to one exact internal call ID and canonical hash of the effective arguments after path canonicalization. Broad task/level reuse is rejected. One-shot consumption, expiry, revoke, and duplicate-insert rejection are enforced; SQLite consumption now uses `BEGIN IMMEDIATE` so validation, expiry/revoke check, and consumption commit are one transaction. Reconciliation audit insertion and intent transition are also one transaction. Reconciliation inspection remains available for an already-claimed side effect.

Gate governance progress: `spec/v2/GATE_STATUS.json` schema v3 uses `IMPLEMENTED`, `INTEGRATED`, and `VERIFIED`; only `VERIFIED` is accepted as complete. B11 runtime transition evidence and E31 exact-head CI evidence are tracked separately; current Phase 3.5/4/5 acceptance gates are now all verified, with Phase 6/7 deferred requirements listed separately.

Responsibility boundary: B11 is the runtime guarantee that critical Controller
state transitions use `commit_transition()` with crash/restart coverage. E31 is
the repository/CI guarantee that the exact commit is checked and its test
evidence is retained. Passing one does not promote the other.

## Gate D — Provider contract

合格条件:

- text、multi-tool、sequential result、malformed、timeout、401/403、429、quota、output limit を共通 harness で検査する。
- live probe は capability matrix へモデル名・時刻・結果・失敗分類を記録する。

進捗: ToolSpec の input schema を Provider-neutral `tool_definitions` として ModelRequest に渡し、Gemini `functionDeclarations` / Ollama `tools` payload へ変換する契約を実装済み。Ollama `qwen3:8b` Controller E2EとGemini live contractを確認済み。

Provider error progress: `ProviderError` now carries category, retryable, and optional HTTP status; Controller preserves authentication, rate-limit, transport, and decode categories instead of collapsing them to `provider_decode`.

## Gate E — promotion / deferred work

Gate A〜D の未達を一覧化し、外部依存項目は保留理由と再開条件を残した。
Phase 6A〜6Eの実装・証拠は `GATE_STATUS.json` Stage Fへ移行済み。

Dependency boundary: v2 development installs `requirements-v2-dev.txt` only; legacy v1 replay dependencies are isolated in `requirements-v1-legacy.txt` and are not prerequisites for the kernel test job.
