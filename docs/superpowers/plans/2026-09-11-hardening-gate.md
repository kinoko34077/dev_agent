# Residual Hardening Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the remaining resource-repair, liveness, qualification, planner, and external-gate issues without changing the established v2 authority boundaries.

**Architecture:** Keep the existing ResourceLedger, ProviderDispatcher, Queue, QualificationResolver, Planner/TaskGraph, DevFarm, and Commander contracts. Add only narrow projections, wake identities, typed dependency metadata, and explicit operator/external evidence; do not add a scheduler, database, or agent framework.

**Tech Stack:** Python 3.10/3.11, SQLite, pytest, existing CLI/DevFarm/Commander tooling, GitHub CLI when authenticated.

**Spec:** User-provided `dev_agent 残件Hardening指示書`; repository `AGENTS.md`, `docs/CURRENT_STATE.md`, `docs/SYSTEM_MAP.md`, `spec/v2/`, and relevant requirements.

## Global Constraints

- Preserve task lifecycle, budget, quota, approval, lease/fencing, UNKNOWN/reconciliation, and Provider fallback semantics.
- Keep the v2 branch `v2/bootstrap`; `main` is frozen v1.
- Use TDD: add a focused failing regression before each production behavior change.
- Do not add a new scheduler, state machine, database, or Agent framework.
- Do not promote G6O1 or fabricate live-provider/GitHub evidence.
- Production Resource mutation requires explicit operator migration/repair; normal startup remains non-destructive.

---

### Task 1: All-lanes saturation wake

**Files:**
- Modify: `src/dev_agent/providers/dispatch.py`, `src/dev_agent/runtime/model_turn.py`, `src/dev_agent/runtime/controller.py`, `src/dev_agent/operation.py`
- Test: `tests/v2/test_provider_saturation_and_wake.py` and existing provider/queue tests

- [x] Add a failing regression showing a task parked while multiple eligible bindings are saturated wakes when any one lane recovers.
- [x] Preserve concrete single-binding wake reasons and introduce only a stable pool wait identity for multi-lane saturation.
- [x] Carry the saturated binding set through ProviderDispatcher without importing runtime code into providers.
- [x] Wake pool waits on capacity recovery and let ResourceRouter reselect; do not busy-poll or wake unrelated waits.
- [x] Run the focused saturation and queue tests, then commit the independently testable slice (`e94f308`).

### Task 2: Qualification confidence admission

**Files:**
- Modify: `src/dev_agent/resources/qualification.py` and the routing projection boundary
- Test: qualification projection/router tests

- [x] Add failing tests for high/current admission, low/medium rejection, expired rejection, and invalid confidence fail-closed behavior.
- [x] Keep raw qualification evidence available for inspection/repair while making Production routing require the documented confidence threshold.
- [x] Run the qualification/resource focused cluster and commit (`809b728`).

### Task 3: Typed Planner dependency semantics

**Files:**
- Modify: `src/dev_agent/operation_planning.py`, TaskGraph/dependency schema owner, relevant docs/spec
- Test: planner dependency and restart persistence tests

- [x] Add failing tests for `ARTIFACT_READY`, `TASK_COMPLETED`, and `CODE_INTEGRATED` release conditions, including integration revision proof.
- [x] Preserve backward compatibility for existing dependency records with an explicit safe default.
- [x] Persist and validate dependency type and reject cycles/invalid integration evidence.
- [x] Run planner/state focused tests and commit (`f5155d6`).

### Task 4: Regression and evidence gate

- [x] Run `python -m pytest tests/v2 -q`, architecture checks, and read-only gate checks (`636 passed, 1 skipped`; `ARCHITECTURE_PASS`; G6O1 remains blocked).
- [x] Update Current State, Traceability, relevant specs, and Changelog with only verified evidence.
- [ ] Re-check exact HEAD and attempt GitHub Actions inspection with authenticated `gh`; report external auth blockers truthfully.

### Task 5: Real Cloud L1 Commander dogfood

- [x] Revalidate current qualification, billing, activation, and credentials immediately before dispatch.
- [x] Create a non-protected narrow docs task with zero outbound repository files and a real Gemini L1 assignment (`hardening-cloud-l1-008`).
- [x] Collect proposal, Host Verification (`5 passed`), metrics, and Git-backed integration evidence (`34bfef8`); never auto-merge or accept model self-report as proof.
- [x] Record the first attempt's Windows socket blocker and the remaining Commander `.devfarm` integration-writeback authorization boundary without fabricating success.

### Task 6: External gates

- [ ] Inspect GitHub branch protection/rulesets with authenticated `gh`; if unavailable, record `BLOCKED_EXTERNAL / USER_ADMIN_ACTION`.
- [ ] Keep Host Verification trust level explicit (`HOST_CONTAINED` unless an OS sandbox is actually available); do not label containment as a sandbox.
- [ ] Synchronize docs only after the code and evidence are verified.
