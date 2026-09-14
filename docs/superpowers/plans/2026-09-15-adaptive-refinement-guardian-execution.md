# Adaptive Refinement and Guardian Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the existing v2 development path from foundation-only Guardian journaling toward bounded Worker refinement and deterministic Guardian process execution, while preserving the existing Task, Provider, Recovery, Authority, UNKNOWN/reconciliation, Compression, and D9 proposal-only boundaries.

**Architecture:** Extend the existing intelligence escalation and DevFarm evidence primitives with explicit failure classification, bounded refinement decisions, and usage accounting. Extend `src/dev_agent/coordination/` with a static-profile process execution boundary that consumes already-authorized `ControlRequest` records and journals every effect through the existing `GuardianActionService`. Do not add a scheduler, retry state machine, model optimizer, or second Task lifecycle. Guardian process tests use injected deterministic fakes before any real foreground process adapter is enabled.

**Tech Stack:** Python 3.10+, existing dataclasses/enums, stdlib `subprocess` only behind a static launch-profile boundary, existing Coordination SQLite store and immutable artifacts, existing DevFarm Supervisor/Worker/Host Verification APIs, pytest, architecture check, compileall, and GitHub Actions exact-head evidence.

**Spec:** `dev_agent 次段階実装指示書 — Worker効率化・Adaptive Refinement・Guardian実動・D9 Real Repair` (2026-09-15 user instruction).

## Global Constraints

- Re-check the current branch, HEAD, worktree, Current State, roadmaps, Gate status, and unfinished Commander plans before each independently verifiable slice.
- Preserve `v2/bootstrap`, existing Task/Scheduler/Operation/Provider/Recovery/Host Verification/Authority boundaries, and the D9 proposal-only boundary until all Guardian prerequisites are independently verified and a new explicit Human approval is present.
- Worker-eligible narrow production tasks, parsers, serializers, tests, fixtures, docs, and mechanical refactors default to the qualified Free Worker. Codex owns architecture, security, authority, recovery, process lifecycle, final review, and integration.
- Do not add a second Task scheduler, retry engine, Agent framework, process killer, autonomous loop, model optimizer, or independent MCP/Compression authority.
- Keep `UNKNOWN` external effects reconciliation-only. Never turn an ambiguous process/provider result into a blind replay or failover.
- Never log or persist credentials, raw Provider responses, raw AI conversations, or full sensitive payloads. Use bounded digests, references, and safe failure metadata.
- Do not run live Gemini, Compression, paid-provider, MCP-wire, external Codex-session, or D9 real-mutation calls for deterministic foundation slices unless a later slice explicitly requires it and the relevant authority is present.
- Use `apply_patch` for tracked-file edits, focused tests before production behavior, and small commits. Push only verified slices to `origin/v2/bootstrap`.

---

## Stage A — Current baseline and drift closure

- [ ] Re-run branch/HEAD/dirty status, Gate JSON validation, unfinished-plan status, focused regression, architecture, compileall, and exact-head CI. Record actual values without treating this plan's snapshot as current.
- [ ] Replace the stale `Current HEAD` wording in `docs/CURRENT_STATE.md` with the repository convention for the latest implementation commit before a documentation-sync commit. Remove accidental patch-marker characters and preserve the truthful Compression, D4, D5, D7, D8, D9, G6O1, and Guardian boundaries.
- [ ] Update `docs/SYSTEM_MAP.md` and `docs/V2_EXECUTION_PLAN.md` to describe the verified bounded Guardian action journal while explicitly keeping process launch, signalling, restart, drain, pinned runtime, rollback, and OS-service work unverified.
- [ ] Confirm `G6O1` remains `DEFERRED_FROZEN`/`NOT VERIFIED`/non-blocking and that Compression live availability remains truthful; do not promote either from local artifacts.
- [ ] Run documentation/reference checks, `git diff --check`, architecture, and compileall; commit and push the documentation-only slice before runtime changes.

## Stage B — Adaptive Refinement policy

- [ ] Add failing tests for a bounded, serializable failure classification covering `FORMAT_PATCH`, `SEMANTIC_TEST`, `CAPABILITY_REASONING`, `PROVIDER_TRANSPORT`, `UNKNOWN_EXTERNAL_EFFECT`, and `SECURITY_EGRESS_AUTHORITY`.
- [ ] Extend the existing `src/dev_agent/intelligence/escalation.py` contract minimally so refinement depth, same-tier model choice, reasoning effort, and intelligence tier are separate bounded decisions. Keep old `EscalationContext`/`EscalationPlan` construction compatible where practical and do not add a second retry state machine.
- [ ] Add a narrow refinement proposal value type for Host-mediated Critic output: bounded findings, locations, required corrections, evidence references, and no integration/approval authority. Add round-trip and size/secret rejection tests.
- [ ] Add deterministic policy tests proving: successful first-pass tasks create no extra model call; format failures choose correction before thinking/tier escalation; semantic failures can request a short L1 Critic; provider failures choose eligible same-tier resources; UNKNOWN and authority failures do not select another model; high thinking is not the default automatic path; model and thinking changes are never combined in one decision.
- [ ] Implement the smallest policy composition over the existing escalation/reassign/REWORK/Host Verification primitives. Use explicit caps for attempts, refinement rounds, distinct bindings, and deadlines; never loop inside an LLM polling cycle.
- [ ] Extend existing Worker metrics only where fields are missing, preserving schema migration/backward-read behavior. Record bounded model calls, refinement rounds, model identities, effort, input/output usage when available, and Codex takeover/direct-implementation reasons without inventing token counts.
- [ ] Run focused intelligence/metrics tests, architecture, and compileall; commit and push the verified policy slice.

## Stage C — Real Worker delegation evidence

- [ ] Identify one real, narrow, non-protected `src/` or `scripts/` production task from the current roadmap or a confirmed defect. Do not fabricate a fixture and do not count test/evidence-only documentation as production Worker evidence.
- [ ] Record a Commander Plan with non-overlapping ownership, dependencies, Worker eligibility, bounded attempts, and the Host egress manifest. Use the qualified Free Worker baseline when admitted.
- [ ] Dispatch through the existing Supervisor `run` path, wait without Codex polling, classify any failure with the Stage B policy, and use bounded REWORK/reassign if justified. Do not take over implementation merely because the first Worker attempt fails.
- [ ] Review the compact ReviewPacket, record a durable Codex decision, integrate only through the existing deterministic Host helper, and preserve exact integration/test evidence. Target evidence must show Worker-originated production code, at least one integrated task, and zero Codex direct implementation for that task.
- [ ] Update Current State/evidence only after the real task is verified; do not call tests/docs-only work productive Worker evidence.

## Stage D — Guardian G1 execution boundary

- [ ] Add failing protocol tests for static launch profiles: role, profile id, executable identity, runtime root, revision, generation, environment profile, and bounded arguments. Reject arbitrary commands, path escape, protected roots, stale generation, and unsupported actions.
- [ ] Implement a public Guardian process-execution boundary under `src/dev_agent/coordination/` that accepts only validated `START`, `STOP`, and `RESTART` requests and static profiles. It must be injectable for deterministic tests and must route result journaling through `GuardianActionService`; no mailbox message may directly execute a command.
- [ ] Add deterministic fake process-runtime tests for successful start/stop/restart, duplicate idempotency, stale generation, policy rejection, executor exception to `UNKNOWN`, and interrupted `EXECUTING` reconciliation. Ensure no `UNKNOWN` action is replayed automatically.
- [ ] If a real foreground adapter is added, keep it opt-in, profile-bound, revision/generation-pinned, bounded, and free of arbitrary user-supplied shell text. Do not claim OS-service or daemon evidence from a foreground fake.
- [ ] Record G1 foundation evidence and update the owning requirements/traceability/current-state documents without claiming G2–G5 or D9 readiness.

## Stage E — Drain, pinned runtime, rolling restart, and fault drills

- [ ] Add protocol/store composition for `DRAINING` and checkpoint readiness that stops new Task claims through existing Task ownership, but does not create a second scheduler. External effects must close as completed, confirmed-cancelled, or UNKNOWN/reconciliation before restart.
- [ ] Add revision-pinned runtime records and validation for immutable release roots, exact revision, generation, and last-known-good reference. Do not mutate a running checkout in place.
- [ ] Compose bounded rolling restart over the Guardian execution boundary: new generation health/READY must precede old generation disposal where policy permits; generation fencing must reject stale control requests.
- [ ] Add fault-drill tests for start failure, crash during drain, Guardian crash during execution, health failure, old-generation stop failure, duplicate/stale requests, mailbox replay, expired control requests, and rollback idempotency. Persist bounded evidence; never replay unknown effects blindly.
- [ ] Run focused coordination/recovery tests, then full `tests/v2`, architecture, compileall, and exact-head CI. Only promote each stage actually evidenced.

## Stage F — D9 gate review (proposal-only until explicit release)

- [ ] Re-read D9 candidate/preflight evidence and verify Peer identity/lease, mailbox/handoff, Guardian execution, drain/checkpoint, revision pinning, rolling restart, health, rollback, and fault-drill prerequisites independently.
- [ ] Keep D9 real mutation locked unless all prerequisites are verified and the Human explicitly authorizes the bounded mutation. A candidate, approval preflight, or local temporary-Git test alone is not release authority.
- [ ] If released later, use one bounded non-protected reversible candidate, revalidate approval, integrate through Host, create a pinned runtime, drain/restart, health-check, and record rollback evidence. Stop and classify any UNKNOWN or safety failure.

## Verification and delivery checklist

- [ ] Every production behavior change has a focused red/green regression test.
- [ ] Worker delegation is recorded separately from Codex implementation; Worker-eligible Codex work has a concrete reason.
- [ ] `python -m pytest tests/v2 -q`, `python scripts/check_architecture.py`, and `python -m compileall -q src recovery scripts` pass at the applicable checkpoint.
- [ ] Current State, roadmap, requirements, traceability, and evidence are updated only where ownership requires it; old working plans are archived after their evidence is integrated.
- [ ] Report the latest implementation commit before documentation sync and the actual pushed remote HEAD separately; do not create self-hash documentation loops.
