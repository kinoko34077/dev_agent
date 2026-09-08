# Kernel integration hardening

Status: in progress. This gate precedes Phase 6.

## Completed in this slice

- Controller checkpoints persist execution state: messages, normalized tool results, model/tool call counters, next step order, active step, and pending ToolCalls.
- Resume consumes checkpointed pending ToolCalls before making another model request.
- Tool results retain `call_id`, `tool_name`, and status in the next `ModelRequest`.
- Controller always attaches its durable StateStore to ToolRuntime.
- Financial / credential / destructive / external-write tools are denied without an explicit, persisted approval record.
- SQLite and JSON stores persist approvals scoped to task and side-effect level; a record from another task or level cannot authorize a tool call.
- High-risk ToolCalls transition the Task to `WAITING_APPROVAL`; a human actor can persist an approval record and resume by approval ID without re-requesting the model or losing the pending call.
- Approval authorization is exact-call scoped by internal call ID and canonical argument hash; a different pending call or mutated arguments cannot reuse the record.
- Internal ToolCall / ToolResult UUIDs are separate from optional Provider call IDs and preserve both across adapter and tool-result boundaries.
- Side-effecting tools require an idempotency key and durable result store.
- External-write / financial / credential / destructive tools create a durable effect intent before handler execution; a pending intent blocks automatic retry as `reconciliation_required`.
- Effect intent creation occurs only after schema and path preconditions, and a failed atomic claim cannot dispatch a competing handler.
- Task wall-clock deadlines are persisted in checkpoint state; resume cannot reset the total budget.
- Tools that declare a path capability use `PathPolicy` before their handler runs.
- Terminal failures use one Controller transition that persists the Step (when present), checkpoint, Task, and `task.failed` event.
- A crash-injection integration test interrupts immediately after the first durable result of two side-effecting ToolCalls; resume executes each handler exactly once and forwards both normalized results to the following model request.
- Crash-injection tests cover `before_model`, `pending_tools`, `after_tool_result`,
  `after_tools`, terminal `after_model`, and `failure` checkpoints; resume does
  not duplicate the completed tool or terminal event.
- The Ollama adapter maps `max_output_tokens` to the provider runtime output bound (`options.num_predict`); the local `/api/tags` probe currently receives connection refused, so D23 remains open.
- Gemini HTTP failure classification is covered for missing credentials, authentication (401/403), rate limiting (429), transport errors, and malformed provider responses.
- Independent Recovery validation rejects orphan steps/checkpoints, malformed tool results, unknown effect-intent states, and unsupported schema versions.
- ToolSpec input schemas are carried as Provider-neutral `tool_definitions` and emitted as Gemini function declarations / Ollama function tools; payload contract tests cover this boundary.
- Typed ProviderError categories are preserved by Controller failure events.
- After a side-effect dispatch, timeout, connection failure, response decode
  failure, output-size failure, and output-schema failure all become
  `reconciliation_required` with a machine-readable `cause`; the associated
  Task enters `WAITING_RECONCILIATION` instead of `FAILED`.
- `Controller` persists task, step, checkpoint, ToolResult, and one or more
  events through `StateStore.commit_transition()` for critical runtime
  transitions, including completion, failure, approval wait, reconciliation
  wait, and each ToolResult.
- `ToolSpec` can require subprocess isolation for untrusted/generated/process
  handlers.  The worker uses an importable top-level handler, bounded IO, and
  process-tree termination on timeout; trusted in-process handlers remain a
  soft-timeout compatibility path.
- Event payloads classify sensitive keys, redact common secret formats, cap
  individual strings, and replace oversized payloads with a digest reference.
- An explicitly injected `EventArtifactStore` can retain already-sanitized
  oversized payloads by content-addressed reference with root-bound reads and
  expiry purge; artifact roots remain an explicit Recovery/retention input.
- The Provider contract harness verifies model-generated ToolCalls, sequential
  calls, normalized ToolResults, and a final response through the neutral protocol.
- A live Gemini `gemini-2.5-flash` probe completed text plus model-generated
  ToolCall, ToolResult, and final response roundtrip on 2026-09-08 JST; the
  observed capability is recorded in `PROVIDER_CAPABILITY_MATRIX.json`.
- Input token estimates, provider-reported output/cost usage, task
  cancellation, and graph limits are enforced or explicitly represented as
  deferred contracts in the hardening plan.
- Recovery now supports validated atomic backup and restore, non-destructive
  Git diagnostics, last-known-good recording, rollback planning, and explicit
  permission gates for rollback/repair-branch mutation.
- Gate status schema v3 distinguishes `IMPLEMENTED`, `INTEGRATED`, and
  `VERIFIED`; only `VERIFIED` satisfies a gate.  Legacy `PASS` is accepted by
  the checker only for schema v1 callers.

## Still required before Phase 6

- Live operator use of persisted diagnostic artifacts remains a later Recovery
  drill; the exact-head CI matrix now passes on the recorded evidence head.
- Approval-wait/resume is now available through `Controller.resume(task_id, approval_id=...)`; a higher-level UI/API for presenting pending approvals remains required.
- Local-model qualification that verifies visible response quality as well as the hard output bound; the installed `qwen3:0.6b` failed this narrow probe because it spent the small output budget on a thinking trace.
- Local Ollama service/model qualification and real Tool-call E2E (D23/D24).
- Expanded contract harness: multi-tool, sequential result, malformed response, timeout, rate-limit, quota, and limits.
- Persisted CI test-result ingestion and an operator-run rollback/repair drill.

## Recovery progress

- SQLite schema and task payload validation is now available through the independent `recovery/validate_sqlite_state.py` CLI.
- Configuration, Git health, last-known-good state, rollback, and repair-branch
  checks are implemented locally; destructive rollback and repair-branch drills
  remain explicit operator actions before Phase 6.
- Persisted JUnit XML reports can be validated independently with
  `recovery/test_results.py` or `recovery/diagnose.py --test-report`; both v2
  workflows emit and upload the report artifact.
- Event artifact roots can be checked independently with
  `recovery/validate_artifacts.py` or `recovery/diagnose.py --artifact-root`;
  the check validates content digests, byte lengths, metadata, and missing
  payloads without importing Runtime or Provider code.
