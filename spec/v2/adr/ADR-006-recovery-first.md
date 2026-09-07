# ADR-006: self-repair より recovery を先行

- Status: accepted
- Decision: 独立 Recovery CLI を通常 Agent Runtime より先に実装する。
- Reason: 壊れた Runtime 自身に修復を依存させないため。
- Consequence: 最小診断・状態検証・rollback の経路を先に維持する。
