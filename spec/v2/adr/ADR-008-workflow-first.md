# ADR-008: workflow-first hybrid

- Status: accepted
- Decision: 既知の反復作業は Fixed / Parameterized Workflow、未知の探索だけ Agent に委任する。
- Reason: 不要な推論費用と無制限 loop を減らし、成功処理を再利用可能にする。
- Consequence: Workflow resolver と Agent planner は別責務にする。
