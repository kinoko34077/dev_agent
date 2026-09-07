# 要求仕様（Phase 0 ベースライン）

- REQ-001: v2 Runtime は `src/dev_agent/` に置き、v1 Runtime を import しない。
- REQ-002: 初期 Kernel はネットワークなしの FakeProvider で検証可能である。
- REQ-003: Task、Step、ToolCall、ToolResult、Event を typed protocol として直列化できる。
- REQ-004: Controller は停止、retry、権限、approval、予算、状態遷移を所有する。
- REQ-005: 不正な Provider 応答と上限超過は分類済みの terminal failure になる。
- REQ-006: 実行前後に checkpoint と監査イベントを保存する。
