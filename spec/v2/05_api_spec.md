# 内部 API 仕様（alpha0）

Provider adapter は `ModelProvider.request(ModelRequest) -> ModelResponse` のみを Core に公開する。Tool は `ToolRegistry.resolve(name)` と `ToolRuntime.execute(ToolCall) -> ToolResult` を経由する。永続化は `StateStore` の Task / Step / Event / checkpoint 操作に限定する。

Provider SDK の response object、例外型、function-calling schema は adapter 内で normalized protocol に変換する。
