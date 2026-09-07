# 挙動仕様（alpha0）

- Runtime loop は再帰呼出しではなく、一回の Step を checkpoint 前後で処理する反復 state machine とする。
- Provider 応答の transport / decode / schema validation / tool execution を別々に記録する。
- ToolCall は registry を経由し、未登録・引数不正・policy denied は実行しない。
- 同じ checkpoint を再処理しても、idempotency key がある副作用を二重実行しない設計余地を残す。
- 失敗時も Task、Step、Event の診断情報を失わない。
