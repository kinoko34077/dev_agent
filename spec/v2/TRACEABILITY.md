# 追跡表（Phase 0 ベースライン）

| ID | 要求 / 不変条件 | 実装領域 | 試験 / Gate |
| --- | --- | --- | --- |
| INV-001, REQ-002 | Provider independence / offline kernel | `providers/fake`, `domain` | alpha0 FakeProvider |
| INV-002, REQ-005 | bounded execution | `runtime/limits` | limit / fault tests |
| INV-003, INV-004, REQ-003, REQ-006 | explicit durable state | `state`, `domain` | serialization / resume |
| INV-005 | auditability | `state/events` | event trace |
| INV-008, INV-013 | protocol / rescue boundary | `providers`, `recovery` | import / adapter contract |
| INV-009, INV-012 | reversible change / human approval | `policy`, Git workflow | policy Gate |
| INV-010, INV-011 | hard budget / reserve | `resources` | survival Gate |
| INV-015 | workflow first | `workflows` | resolver Gate |
