# ADR-005: observed contract を能力の根拠にする

- Status: accepted
- Decision: Provider capability はドキュメントの記載ではなく、実 API Contract Probe の観測結果で確定する。
- Reason: Function Calling や response schema は SDK / endpoint ごとに実挙動が異なる。
- Consequence: live probe の結果と compatibility version を記録し、未観測能力を前提にしない。
