# ADR-003: Runtime の再帰禁止

- Status: accepted
- Decision: agent / main Runtime の再帰呼出しを禁止し、反復 Controller と Task Graph を使う。
- Reason: 有限性、checkpoint、cycle 検出、resume を call stack から切り離す。
- Consequence: depth と child 数はデータとして制限し、global recursion flag は移行しない。
