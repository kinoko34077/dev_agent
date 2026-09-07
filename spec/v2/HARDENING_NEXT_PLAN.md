# Hardening next plan

目的は、外部サービスが停止してもローカルで検証可能な境界を先に完了し、外部依存の項目を明確な保留として後回しにできる状態を作ること。

## Gate A — durable state / recovery（先行）

合格条件:

- 正常・承認待ち・失敗・完了の SQLite 状態を独立 validator が読める。
- 必須テーブル、task payload、checkpoint state、approval scope の破損を検出する。
- validator は通常 Runtime / Provider を import せず、ネットワークなしで実行できる。

失敗時の扱い: 状態を修復・上書きせず `FAIL` と診断し、バックアップからの再検査へ回す。

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

## Gate D — Provider contract

合格条件:

- text、multi-tool、sequential result、malformed、timeout、401/403、429、quota、output limit を共通 harness で検査する。
- live probe は capability matrix へモデル名・時刻・結果・失敗分類を記録する。

## Gate E — promotion / deferred work

Gate A〜D の未達を一覧化し、外部依存項目は保留理由と再開条件を残す。Gate を満たすまで Phase 6（resource / survival / rescue）へ進まない。
