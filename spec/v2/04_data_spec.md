# データ仕様（alpha0）

最低限の typed record:

- `Task`: task_id、parent_task_id、objective、status、depth、limits、timestamps
- `Step`: step_id、task_id、order、kind、status、attempt、refs、timestamps、error_ref
- `ModelRequest`: normalized input、capabilities、allowed tools、response schema、limits
- `ModelResponse`: response_id、provider/model、parts、finish reason、usage、tool_calls、warnings
- `ToolCall`: call_id、tool_name、typed arguments、request/response refs、idempotency key
- `ToolResult`: call_id、status、structured result、side-effect metadata、error
- `Event`: event_id、type、correlation refs、payload、timestamp（append-oriented）

識別子は UUID など衝突しない方式とし、秘密情報をイベントへ平文保存しない。
