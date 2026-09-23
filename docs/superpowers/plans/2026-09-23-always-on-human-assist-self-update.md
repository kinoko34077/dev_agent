# Always-On Human Assist and Self-Update Integration Plan

> **For agentic workers:** Execute this plan task by task. Keep each slice
> narrow, run its focused tests before moving on, and preserve the existing
> authority and recovery boundaries.

**Goal:** Make the existing dev_agent runtime capable of remaining alive while
individual tasks wait for Human input or external reconciliation, expose a
minimal separated Human Proxy / Expert Assist boundary for Codex, and connect
local self-update checks to the existing revision-pinned and rollback
primitives without creating a second scheduler, state store, or retry engine.

**Architecture:** Extend the existing StateStore and TaskStatus model with a
bounded, one-shot-correlated Human interaction record. Add ports and adapters
above the existing OperationService/RuntimeCoordinator and transport-neutral
MCP contracts. Keep Human authority distinct from Codex proposal authority.
Reuse existing rolling, generation fencing, pinned-runtime, health, drain, and
rollback code after its exact APIs are inspected; add only a thin composition
boundary if one is missing. Add a repository-local BAT launcher only; OS
startup registration remains deferred.

**Tech Stack:** Python 3.10/3.11, SQLite StateStore, dataclasses/enums,
existing MCP bounded JSON contracts, existing OperationService and
RuntimeCoordinator, Windows batch launcher, pytest.

## Global Constraints

- Do not add a parallel scheduler, RuntimeCoordinator, StateStore, retry
  framework, Agent framework, or MCP-side authority engine.
- Preserve Host Verification, deterministic integration, approval, budget,
  privacy, effect-intent, UNKNOWN/reconciliation, lease/fencing, and protected
  path boundaries.
- `WAITING_HUMAN` is for a genuine bounded Human question; approval semantics
  remain distinct and existing approval states are not renamed.
- Codex Expert output is always proposal-only. A Codex response is never
  converted into a HumanResponse.
- Human responses must correlate to the exact request and be consumed once.
- Human timeout never implies approval, rejection, or a default decision.
- Never persist credentials, secrets, raw conversations, raw provider output,
  or unbounded exception text. Use existing audit sanitization and bounded
  projections.
- Do not replay UNKNOWN external effects or change formal D9/Phase 8 gates.
- Do not register Task Scheduler, Windows Service, Startup, or OS Guardian.
- Keep edits reversible; do not delete user files. If a temporary artifact must
  be removed, move it to a scoped recoverable location and report it.

## Review Focus

- HumanProxy authority cannot be forged by Expert Assist or a model.
- Unrelated READY work continues while one task waits for Human or
  reconciliation.
- Restart preserves durable request/task state without duplicate dispatch.
- Candidate self-update cannot overwrite a running revision and cannot loop on
  the same failed revision.
- The BAT resolves its own repository root and changes no OS configuration.
- Changed files contain no credential-shaped values or raw sensitive payloads.

---

### Task 1: Add bounded Human interaction contracts and durable records

**Files:** `src/dev_agent/human/` (new bounded contract/port module if no
existing equivalent), `src/dev_agent/domain/protocol.py`,
`src/dev_agent/state/schema.py`, `src/dev_agent/state/sqlite_store.py`,
`src/dev_agent/security/audit.py` only if a small reusable bounded projection
helper is required; tests under `tests/v2/` following existing StateStore and
protocol test conventions.

**Steps:**

1. Write failing tests for `HumanRequest`/`HumanResponse` validation, bounded
   fields, sanitized context, exact request correlation, and one-shot response
   consumption.
2. Add immutable bounded records with `request_id`, `root_id`, `task_id`,
   `attempt_id`, reason/question, sanitized bounded context, required
   authority, allowed answers/response shape, and timestamps. Include
   responder/decision/response fields on the response without storing raw
   conversation data.
3. Add `HumanInteractionPort` with `request_human`, `poll_response`, and
   `consume_response` contracts. Keep it transport-neutral and injectable.
4. Extend the existing SQLite schema through a sequential migration for
   `human_requests` and response/consumption state. Do not create another
   database or store. Make duplicate consume and wrong-request response
   correlation deterministic failures.
5. Run focused RED/GREEN tests, schema migration tests, architecture checks,
   compileall, and the full `tests/v2` suite.

### Task 2: Park and resume only the Human-blocked task

**Files:** `src/dev_agent/domain/protocol.py`, existing operation/queue/state
modules discovered by searching for `WAITING_RECONCILIATION`,
`maintenance_tick`, and task wake transitions; relevant runtime tests.

**Steps:**

1. Add a failing test proving a Human-required task becomes `WAITING_HUMAN`
   durably while unrelated READY work is still claimed and completed.
2. Add the smallest lifecycle transition from a Human-required outcome to the
   new waiting state, preserving existing approval and reconciliation states.
3. Add exact-request response correlation and fresh continuation semantics;
   do not reuse the previous attempt identity or consume a response twice.
4. Add restart coverage proving the request and waiting task survive a normal
   RuntimeCoordinator restart and no duplicate dispatch occurs.
5. Verify timeout keeps the task waiting and does not synthesize approval or
   rejection. Run focused and full regression tests.

### Task 3: Implement the minimal Codex MCP Human Proxy and Expert Assist split

**Files:** existing `src/dev_agent/mcp/contracts.py` and `runtime.py`,
`src/dev_agent/mcp/` adapter modules, existing Codex backend/JSONL modules,
new tests in `tests/v2/`.

**Steps:**

1. Inspect the current Codex backend and MCP runtime APIs and write failing
   contract tests before adding transport code.
2. Add only a bounded request/response codec/client needed for Human Proxy and
   Expert Assist, with correlation IDs, size limits, sanitized errors, and no
   credentials in payloads or logs. Reuse existing MCP limits and authorizer.
3. Implement `CodexMcpHumanAdapter` that submits a `HUMAN_REQUIRED` request
   and accepts only an explicitly correlated Human response. Codex text alone
   must not become a HumanResponse.
4. Implement `CodexMcpExpertAdapter` with `PROPOSAL_ONLY` authority and
   bounded context. Its output is a proposal passed back through Host
   validation; it cannot approve, integrate, mutate Git, or promote a Gate.
5. Add anti-spoof, timeout, malformed response, duplicate response, and
   authority separation tests. Keep unconnected MCP planning tools unchanged.

### Task 4: Connect existing RuntimeCoordinator self-heal behavior

**Files:** `src/dev_agent/operation_runtime.py`, existing operation lifecycle
and refinement/review integration modules, `scripts/devfarm_runtime_coordinator.py`,
tests for runtime restart, Human waiting, reconciliation, and self-heal.

**Steps:**

1. Write a failing integration test for idle serve, task wake, local repair,
   Human parking, and unrelated task continuation.
2. Connect the new port/state transitions to the existing `serve` loop and
   maintenance tick without adding another loop owner or scheduler.
3. Preserve existing bounded repair/refinement and reviewer proposal-only
   semantics. Format/test repair remains automatic only after Host
   Verification; UNKNOWN/reconciliation remains non-replayable.
4. Add process restart and generation-fencing assertions, then run the
   existing RuntimeCoordinator suite plus full regression.

### Task 5: Reuse existing release/rollback primitives for local self-update

**Files:** exact existing rolling/revision-pinned runtime/recovery modules
identified during preflight, a minimal composition module only if required,
and corresponding tests under `tests/v2/`.

**Steps:**

1. Inspect and map the existing drain, checkpoint, candidate health,
   promotion, generation, LKG, and rollback APIs before editing.
2. Write failing tests for good candidate promotion, bad candidate rollback,
   queue/waiting-state preservation, duplicate-dispatch prevention, and
   durable `candidate_failed` suppression for the same revision.
3. Compose those existing primitives into a bounded self-update operation.
   Do not overwrite a running checkout, change trusted source rules, or add a
   separate updater state machine.
4. Keep self-update authority changes, protected-path changes, credentials,
   rollback authority, and trusted-source changes Human-required.
5. Run focused update tests and all normal verification checks. If an existing
   primitive is incomplete, record the exact blocker rather than weakening it.

### Task 6: Add the thin Windows launcher

**Files:** repository-root `start-dev-agent.bat`, launcher tests or a bounded
   documented smoke procedure if the existing suite has no BAT harness,
   operator documentation.

**Steps:**

1. Confirm the exact current Coordinator serve module/arguments from code.
2. Add a `%~dp0`-based launcher that resolves the repository root, uses the
   existing Python entrypoint, preserves the exit code, and makes no registry,
   scheduler, dependency, Git, or credential changes.
3. Test invocation from a different working directory, including a path with
   spaces where available, and verify no duplicate registration/state mutation.

### Task 7: Evidence and documentation synchronization

**Files:** `docs/CURRENT_STATE.md`, `docs/V2_EXECUTION_PLAN.md`,
`docs/V2_DETAILED_ROADMAP.md`, `docs/SYSTEM_MAP.md`, relevant requirements/ADR
documents, bounded evidence JSON under `spec/v2/evidence/`.

**Steps:**

1. Record only verified behavior: Core always-on local operation, durable
   Human waiting, interim Codex MCP Human Proxy status, proposal-only Expert
   Assist, BAT readiness, and local self-update/rollback results.
2. Keep Discord direct integration, OS startup registration, Production
   Deployment, Phase 8 LIVE_ACTIVATION, and protected authority changes
   explicitly deferred unless independently evidenced.
3. Run secret-shaped pattern checks on changed artifacts and ensure evidence
   contains bounded projections only.
4. Run focused tests, `python -m pytest tests/v2 -q`, architecture check,
   compileall, `git diff --check`, commit, push, and exact-head GitHub checks.

### Task 8: Operational handoff

**Files:** final evidence and operator docs only.

**Steps:**

1. Verify `start-dev-agent.bat` starts the existing Coordinator in foreground
   mode and that idle operation remains alive without busy looping.
2. Verify normal local self-heal, Human waiting, reconciliation waiting, and
   unrelated READY work behavior from durable state.
3. Report the exact remote HEAD, push-before SHA, implementation status,
   tests, architecture, compile, CI, formal Gate impact, deferred blockers,
   and the next concrete roadmap action.

