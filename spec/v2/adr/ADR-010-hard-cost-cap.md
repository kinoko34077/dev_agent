# ADR-010: 初期外部費用の上限

- Status: accepted (subject to user change)
- Decision: 初期の追加外部費用は月 JPY 2,000 を hard cap とし、成長予算の拡張は realized net profit と human approval を要する。
- Reason: Provider 依存・無制限消費を避け、Recovery Reserve を残す。
- Consequence: governor は provider 呼出前に判定し、Agent の判断で cap を超えられない。
