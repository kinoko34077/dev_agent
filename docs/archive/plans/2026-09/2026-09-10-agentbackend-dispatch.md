# AgentBackend dispatch infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect a thin, authority-preserving AgentBackend dispatcher to the existing dev_agent Control Plane and prove its lifecycle with a deterministic fake backend.

**Architecture:** Keep `AgentBackend` separate from `ModelProvider`. A small dispatcher validates the existing durable task/scope/approval boundary, records dispatch identity and lifecycle evidence through the existing StateStore/effect-intent primitives, and delegates only the external session operation to an injected backend. Unknown outcomes remain UNKNOWN/RECONCILING and are never replayed automatically.

**Tech Stack:** Python 3.10/3.11, dataclasses, Protocol, existing SQLiteStateStore, pytest, existing effect/audit/reconciliation primitives.

**Spec:** `docs/requirements/multi-free-provider/05-agent-backend-codex-mcp-and-authority.md`, `spec/v2/05_api_spec.md`, `spec/v2/06_implementation_spec.md`

## Global Constraints

- `AgentBackend` is not a `ModelProvider` and never enters `ProviderRegistry` or `ResourceRouter`.
- Task state, Scheduler, lease/fencing, budget, quota, approval, protected paths, audit, cancellation policy, Recovery, and Gate remain owned by dev_agent.
- No new scheduler, durable state machine, Agent framework, MCP, Codex-specific types in the kernel, or automatic retry of UNKNOWN effects.
- External workspace access is scoped; official `v2/bootstrap` is never directly edited by a backend.
- Existing public contracts and schema versions remain compatible.

---

### Task 1: Audit and tighten the generic backend contract

**Files:**
- Modify: `src/dev_agent/backends/protocol.py`
- Test: `tests/v2/test_agent_backend_protocol.py`

**Interfaces:**
- Consumes: existing `AgentBackendIdentity`, `AgentBackendScope`, `AgentBackendRequest`, `AgentBackendSession`, `AgentBackendEvent`, `AgentBackendResult`, `AgentBackendStatus`.
- Produces: restart-safe `session_id`, ordered event sequence, approval/cancellation/UNKNOWN/RECONCILING statuses, and opaque artifact references without backend-specific schema.

- [x] Add failing tests for backend session identity, non-replayed sequence, approval/cancelling status, and artifact reference validation.
- [x] Run `python -m pytest tests/v2/test_agent_backend_protocol.py -q`; confirm the new assertions fail for missing boundary behavior.
- [x] Implement only generic validation and status representation; do not add Codex fields.
- [x] Re-run the focused test and the existing protocol regression.

### Task 2: Add the AgentBackend dispatch authority boundary

**Files:**
- Create: `src/dev_agent/backends/dispatcher.py`
- Modify: `src/dev_agent/backends/__init__.py`
- Test: `tests/v2/test_agent_backend_dispatcher.py`

**Interfaces:**
- Consumes: `AgentBackend`, typed request/session/result, existing StateStore/task lookup, existing scope/approval/effect identity boundaries.
- Produces: `AgentBackendDispatcher.dispatch(request, backend, *, dispatch_id, attempt)` and durable identity composed from `task_id`, `backend_id`, `backend_session_id`, `dispatch_id`, `attempt`, workspace/scope, and request fingerprint.

- [x] Write failing tests for missing task/scope, denied approval, completed dispatch deduplication, and immutable request fingerprint.
- [x] Run the focused dispatcher tests and confirm they fail because the dispatcher does not exist.
- [x] Implement validation and durable dispatch identity using existing storage/effect primitives; call only `backend.start()` after checks pass.
- [x] Add event/result collection methods that validate session identity and preserve UNKNOWN versus RECONCILING.
- [x] Make COMPLETED idempotent, and reject automatic retry for UNKNOWN/RECONCILING.
- [x] Run dispatcher focused tests and existing state/effect regressions.

### Task 3: Add a deterministic fake backend and restart/reconciliation tests

**Files:**
- Create: `tests/v2/fixtures/fake_agent_backend.py`
- Test: `tests/v2/test_agent_backend_dispatcher.py`

**Interfaces:**
- Consumes: generic `AgentBackend` contract and dispatcher.
- Produces: test-only `FakeAgentBackend` scenarios for start/events/result/cancel, restart lookup, unknown outcome, and reconciliation.

- [x] Write failing tests covering start, ordered events, result, cancel, approval wait, backend death, dispatcher restart, and UNKNOWN→RECONCILING without replay.
- [x] Run the focused tests to verify the intended failures.
- [x] Implement a test-only in-memory deterministic fake with explicit scenario configuration; it must not enter production imports or own production state or scheduler behavior.
- [x] Add restart simulation through reusing the same backend session identifier and a separate dispatcher instance.
- [x] Run focused tests and all `tests/v2`.

### Task 4: Synchronize contract documentation and evidence

**Files:**
- Modify: `docs/CURRENT_STATE.md`
- Modify: `docs/V2_EXECUTION_PLAN.md`
- Modify: `docs/SYSTEM_MAP.md`
- Modify: `spec/v2/05_api_spec.md`
- Modify: `spec/v2/06_implementation_spec.md`
- Modify: `spec/v2/TRACEABILITY.md`
- Modify: `CHANGELOG.md`

- [x] Record the dispatcher and fake lifecycle boundary without claiming Codex App Server live integration.
- [x] Record focused and full regression counts from fresh commands.
- [x] Preserve G6O1 external-blocked status and Evidence routing advisory-only status.
- [x] Run `git diff --check`, JSON/spec checks, and the full v2 regression.
- [x] Commit code/tests and documentation separately, then push `v2/bootstrap`.

## Gaps intentionally deferred

Codex App Server transport/protocol verification, isolated workspace live E2E, Host Verification integration, MCP SDK exposure, Commander schema integration, and AgentBackend production recovery drill are the next batch and are not claimed by this plan.
