# Kernel integration hardening

Status: in progress. This gate precedes Phase 6.

## Completed in this slice

- Controller checkpoints persist execution state: messages, normalized tool results, model/tool call counters, next step order, active step, and pending ToolCalls.
- Resume consumes checkpointed pending ToolCalls before making another model request.
- Tool results retain `call_id`, `tool_name`, and status in the next `ModelRequest`.
- Controller always attaches its durable StateStore to ToolRuntime.
- Financial / credential / destructive / external-write tools are denied without an explicit, persisted approval record.
- SQLite and JSON stores persist approvals scoped to task and side-effect level; a record from another task or level cannot authorize a tool call.
- Side-effecting tools require an idempotency key and durable result store.
- Tools that declare a path capability use `PathPolicy` before their handler runs.
- Terminal failures use one Controller transition that persists the Step (when present), checkpoint, Task, and `task.failed` event.
- A crash-injection integration test interrupts immediately after the first durable result of two side-effecting ToolCalls; resume executes each handler exactly once and forwards both normalized results to the following model request.
- Crash-injection tests cover terminal `after_model` and `failure` checkpoints; resume finalizes the persisted terminal transition without repeating the provider call.
- The Ollama adapter is exercised against the local `/api/chat` endpoint and maps `max_output_tokens` to the provider runtime output bound (`options.num_predict`).
- Gemini HTTP failure classification is covered for missing credentials, authentication (401/403), rate limiting (429), transport errors, and malformed provider responses.

## Still required before Phase 6

- Crash injection at the remaining pre-model and model-response event boundaries.
- Controller-facing approval IDs and human actor records need a dedicated approval-wait/resume API; the low-level boolean compatibility path remains only in `ApprovalPolicy` tests.
- Local-model qualification that verifies visible response quality as well as the hard output bound; the installed `qwen3:0.6b` failed this narrow probe because it spent the small output budget on a thinking trace.
- Real Gemini live contract probe (network reached, but current key/model combination returned HTTP 403; credential/project restriction must be corrected before promotion).
- Expanded contract harness: multi-tool, sequential result, malformed response, timeout, rate-limit, quota, and limits.
- Recovery tools that inspect SQLite state, configuration, Git health, and test results.

## Recovery progress

- SQLite schema and task payload validation is now available through the independent `recovery/validate_sqlite_state.py` CLI.
- Configuration, Git health, last-known-good state, rollback, and repair-branch checks remain required before Phase 6.
