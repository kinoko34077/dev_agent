# ADR-007: 外部 rescue path の独立

- Status: accepted
- Decision: Rescue CLI / MCP は通常 Runtime、router、framework、vector DB へ依存しない。
- Reason: 通常系の広域障害時にも外部 Agent が読み取り・検証・限定修復できる必要がある。
- Consequence: rescue の API と権限を狭く保ち、通常機能を流用しない。
