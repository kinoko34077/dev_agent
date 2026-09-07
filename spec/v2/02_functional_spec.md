# 機能仕様（alpha0）

1. Task を作成し、`queued` から実行可能状態へ遷移する。
2. Controller が ModelRequest を作り、FakeProvider に渡す。
3. 返却された ToolCall を schema / policy 検証する。
4. 登録済み無害 Tool を一回実行し、ToolResult と Event を保存する。
5. ToolResult を含む次の ModelRequest を FakeProvider に渡す。
6. 最終 ModelResponse を保存し、Task を `completed` にする。
7. 各段階で失敗を分類し、上限到達時は `limits_exceeded` として停止する。
