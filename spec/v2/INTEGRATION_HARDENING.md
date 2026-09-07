# Kernel integration hardening

Status: in progress. This gate precedes Phase 6.

## Completed in this slice

- Controller checkpoints persist execution state: messages, normalized tool results, model/tool call counters, next step order, active step, and pending ToolCalls.
- Resume consumes checkpointed pending ToolCalls before making another model request.
- Tool results retain `call_id`, `tool_name`, and status in the next `ModelRequest`.
- Controller always attaches its durable StateStore to ToolRuntime.
- Financial / credential / destructive / external-write tools are denied without explicit approval.
- Side-effecting tools require an idempotency key and durable result store.
- Tools that declare a path capability use `PathPolicy` before their handler runs.
- Terminal failures use one Controller transition that persists the Step (when present), checkpoint, Task, and `task.failed` event.

## Still required before Phase 6

- Crash injection at each persistence boundary, including process interruption after one of several ToolCalls.
- Explicit persisted approval records; no caller-supplied approval boolean at a public Runtime boundary.
- Actual local model endpoint E2E.
- Real Gemini / independent Provider response decoding and live contract probes.
- Expanded contract harness: multi-tool, sequential result, malformed response, timeout, rate-limit, quota, and limits.
- Recovery tools that inspect SQLite state, configuration, Git health, and test results.

## Recovery progress

- SQLite schema and task payload validation is now available through the independent `recovery/validate_sqlite_state.py` CLI.
- Configuration, Git health, last-known-good state, rollback, and repair-branch checks remain required before Phase 6.
