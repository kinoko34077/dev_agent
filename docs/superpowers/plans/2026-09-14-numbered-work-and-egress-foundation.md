# Numbered Work and Host Egress Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate human-readable work addresses, bounded interruption recovery, and Host-owned low-risk egress manifests into the existing Commander and Process Coordination foundation without creating parallel scheduling or authority.

**Architecture:** `task_id`, dependencies, ownership, and existing Commander status remain authoritative. A small pure work-address/resume value layer is stored through existing Plan projections and immutable Coordination artifacts. Egress is a Host-side policy/manifest boundary that reuses existing protected-path, audit, provider, privacy, and approval checks; Worker code receives only an already-allowed manifest.

**Tech Stack:** Python 3.10+, stdlib dataclasses/enum/json/pathlib/hashlib, existing Commander JSON plans, existing Coordination SQLite/artifact store, existing audit/protected-path policy, pytest, architecture and compile checks.

**Spec:** `docs/CODEX_WORK_COORDINATION.md`, `docs/requirements/process-coordination/01-work-address-and-egress.md`, and the preceding Process Coordination plan.

## Global Constraints

- Preserve existing Task/Scheduler/Provider/Resource/Recovery/Host Verification/Approval/Privacy/UNKNOWN boundaries.
- Do not create a second Task scheduler, retry engine, Agent framework, Guardian, MCP authority, or automatic official-branch integration.
- Keep `task_id` as immutable identity; `work_address` is optional display/resume metadata.
- Never send secrets, credentials, protected source, `.env*`, private keys, raw conversations, or raw Provider responses.
- Work on focused safe parser/test/docs tasks may be delegated to a qualified Free Worker; egress/security/authority code remains Host/Codex-owned.
- Use bounded depth, size, attempts, and deadlines. Do not infer external session discovery or replay UNKNOWN effects.

---

### Task 1: Number the current work program and SSOT references

**Files:**
- Create: `docs/CODEX_WORK_COORDINATION.md`
- Create: `docs/requirements/process-coordination/01-work-address-and-egress.md`
- Modify: `AGENTS.md`, `docs/CODEX_COMMANDER.md`, `docs/CODEX_SUPERVISOR.md`, `docs/CODEX_DAILY_DOGFOOD.md`
- Test: documentation link/reference check and existing docs checks

**Interfaces:**
- Consumes: existing Commander Plan, Coordination foundation, immutable artifact rules.
- Produces: one numbered operation contract referenced by Codex entrypoints; no runtime behavior change.

- [x] **Step 1: Record the numbered order**

  Keep Stage A stabilization, Stage B limited refactor, Work Address, Egress, Process Coordination, Guardian, and D9 real repair as numbered gates. Mark only the existing foundation as implemented.

- [ ] **Step 2: Check references**

  Run `rg -n "CODEX_WORK_COORDINATION|work_address|egress" AGENTS.md docs spec/v2` and the repository's documentation link check. Any broken reference is fixed before committing the docs slice.

- [ ] **Step 3: Commit the docs slice**

  Run `git diff --check` and commit only the documentation/plan changes with a `docs:` message.

### Task 2: Add failing Work Address value tests

**Files:**
- Create: `tests/v2/test_process_coordination_work.py`
- Create later: `src/dev_agent/coordination/work.py`

**Interfaces:**
- Consumes: the address grammar and interruption contract in `CODEX_WORK_COORDINATION.md`.
- Produces: tests for `WorkAddress.parse`, `next_child`, `ResumeCapsule`, and bounded interrupt classification.

- [ ] **Step 1: Write the failing tests**

  The tests must prove that `WorkAddress.parse("5-B-8-3")` round-trips, unsafe/empty/lowercase segments fail, numeric and letter children are allocated without collision, and a resume capsule rejects an owned path outside the safe relative-path grammar.

- [ ] **Step 2: Run the focused test and observe the expected missing-module failure**

  Run `python -m pytest tests/v2/test_process_coordination_work.py -q`. It must fail because the production value module has not been implemented yet, not because of a test typo.

### Task 3: Implement the pure Work Address/Resume layer

**Files:**
- Create: `src/dev_agent/coordination/work.py`
- Modify: `src/dev_agent/coordination/__init__.py`
- Test: `tests/v2/test_process_coordination_work.py`

**Interfaces:**
- Consumes: existing coordination validation helpers and `HandoffNote` artifact model.
- Produces: `WorkAddress`, `ResumeCapsule`, `InterruptionMode`, `InterruptFrame`, and bounded next-child allocation. This module performs no I/O, environment reads, network calls, scheduling, or process control.

- [ ] **Step 1: Implement the smallest tested value types**

  Use frozen dataclasses/enums, validate segments with `^[0-9]+$` or `^[A-Z]$`, require positive numeric segments, cap address depth at 16 and capsule fields at existing coordination bounds, and expose deterministic `to_dict`/`from_dict` methods.

- [ ] **Step 2: Make the tests pass**

  Run `python -m pytest tests/v2/test_process_coordination_work.py -q` and then the existing coordination tests. Do not add task transitions or a queue in this module.

- [ ] **Step 3: Delegate an isolated parser/fixture task when eligible**

  If a qualified Free Worker is available, send only the non-sensitive focused test/contract for a parser or fixture refinement with non-overlapping ownership. Host verifies any proposal; a Worker report alone never counts as integration.

### Task 4: Connect Work Address to existing Plan/Coordination records

**Files:**
- Modify: `scripts/devfarm_commander.py`, `src/dev_agent/coordination/service.py`, existing plan schema only where optional backward-compatible fields are supported
- Test: `tests/v2/test_devfarm_commander.py`, `tests/v2/test_process_coordination_service.py`

**Interfaces:**
- Consumes: `WorkAddress`, `ResumeCapsule`, existing plan revision CAS, `CoordinationArtifactStore`, and `HandoffNote`.
- Produces: optional task `work_address` and bounded checkpoint/handoff methods; old plans containing only UUID/status/ownership remain valid.

- [ ] **Step 1: Add failing compatibility tests**

  Prove old plans normalize unchanged, duplicate active addresses are rejected within a plan, and a checkpoint artifact survives service reopen without changing Task ownership or dependency semantics.

- [ ] **Step 2: Implement optional projections through public boundaries**

  Store only validated address/capsule references in the existing plan projection or artifact reference. Do not put mutable resume state in a shared Markdown file and do not infer readiness from address order.

- [ ] **Step 3: Verify focused Commander/Coordination behavior**

  Run the two focused test modules, `python scripts/check_architecture.py`, and `python -m compileall -q src recovery scripts`.

### Task 5: Specify Host Egress policy and manifest tests

**Files:**
- Create later: `src/dev_agent/egress.py` or the existing security-owned module selected by the implementation boundary
- Create: `tests/v2/test_devfarm_egress.py`
- Optional data: `spec/v2/EGRESS_POLICY.json` only if the existing config convention supports a tracked non-secret policy

**Interfaces:**
- Consumes: existing `is_protected_path`, audit redaction/secret patterns, manifest path rules, Provider approval and sensitivity classifications.
- Produces: tests for `ALLOW`, `REVIEW`, `DENY`, per-file digest/size, standing grant scope, symlink/path escape, secret-shaped content, and transport/policy/content category separation.

- [ ] **Step 1: Write the failing security-boundary tests**

  Tests must show that a clean `src/` file may be allowed by an explicit low-risk grant, `.env`, private-key content, protected paths, path escapes, and oversized files are denied, unknown extensions can require review, and a changed byte changes the manifest digest.

- [ ] **Step 2: Run the focused tests and confirm they fail for the missing Host egress boundary**

  Run `python -m pytest tests/v2/test_devfarm_egress.py -q`; do not implement policy logic before observing the expected failure.

### Task 6: Implement Host-owned Egress Manifest and safe Worker integration

**Files:**
- Create: the security-owned egress module selected in Task 5
- Modify: `scripts/devfarm_worker.py` only at the existing outbound input boundary
- Test: `tests/v2/test_devfarm_egress.py`, `tests/v2/test_devfarm_manifest.py`, relevant worker tests

**Interfaces:**
- Consumes: `StandingEgressGrant`, explicit task outbound paths, destination/provider identity, existing manifest approval.
- Produces: `EgressManifest` with `ALLOW/REVIEW/DENY`, bounded `ManifestEntry` hashes, and an optional explicit `egress_manifest` reference used before external Worker dispatch.

- [ ] **Step 1: Implement policy validation and content scan**

  Reuse protected-path and audit primitives. Resolve the repository root, reject symlink escapes and deny patterns, read bytes only after containment checks, compute SHA-256/size, scan secret-shaped content, and return structured non-secret reasons.

- [ ] **Step 2: Integrate only at the existing outbound boundary**

  A new explicit egress grant/manifest is required for the new path. Legacy manifests remain backward-compatible but do not receive a broader implicit permission. `ALLOW` is required before the selected outbound bytes are assembled for a remote Provider.

- [ ] **Step 3: Verify no secret/raw payload leakage**

  Assert logs and result artifacts contain policy id, path, size, hash, and decision only; never assert or persist source body, token, credential, or raw Provider response.

### Task 7: Evidence and document synchronization

**Files:**
- Modify: `docs/CURRENT_STATE.md`, `docs/V2_EXECUTION_PLAN.md`, `docs/V2_DETAILED_ROADMAP.md`, `spec/v2/TRACEABILITY.md`
- Create: bounded `spec/v2/evidence/work-address-egress-<date>.json`

**Interfaces:**
- Consumes: focused/full test output and actual implementation commits.
- Produces: truthful current capability, explicit unimplemented Guardian/D9 scope, traceability rows, and no raw payload.

- [ ] **Step 1: Record exact evidence**

  Record work-address round-trip, checkpoint persistence, cross-plan ownership protection, and egress decisions with hashes/counts only. Do not promote D9 or Guardian from protocol existence.

- [ ] **Step 2: Run the full local gates**

  Run `python -m pytest tests/v2 -q`, `python scripts/check_architecture.py`, and `python -m compileall -q src recovery scripts`. Reconcile failures before commit.

- [ ] **Step 3: Push the verified checkpoint and inspect exact-head CI**

  Push to `origin/v2/bootstrap` only after the local evidence is fresh. Confirm the required exact-head checks and update `CURRENT_STATE.md` to the actual pushed SHA.

### Task 8: Continue the numbered program only after Task 7

**Files:**
- Modify later: `src/dev_agent/coordination/`, Guardian/runtime files selected by a separate plan
- Test later: bounded fault/restart tests

**Interfaces:**
- Consumes: verified Work Address, egress manifest, peer/mailbox/handoff foundation.
- Produces: future D10 Guardian/drain/revision gates and then D9 controlled mutation; it must not add any parallel scheduler or weaken Human/Host authority.

- [ ] **Step 1: Implement Process Coordination/Guardian gates in separate reviewed slices**

  Keep ControlRequest validation, generation fencing, drain/checkpoint, revision pinning, health, rollback, and fault evidence separate. Do not begin D9 real mutation before all gates are verified.

- [ ] **Step 2: Keep deferred work deferred**

  G6O1, paid providers, OpenAI/Claude API, Compression, MCP wire, Discord/UI, auto-production deploy, and unbounded autonomous loops remain outside this plan.

---

## Verification checklist

- [ ] Numbered work program is referenced by AGENTS and daily/Commander/Supervisor documents.
- [ ] Old Commander plans remain backward-compatible.
- [ ] Work addresses are validated, collision-safe, and separate from dependency/ownership.
- [ ] Resume capsules are bounded immutable references, not raw conversation.
- [ ] Ambiguous user input does not interrupt active work.
- [ ] Active cross-plan ownership remains fail-closed.
- [ ] Egress is Host-owned, content-scanned, hashed, bounded, and secret-free.
- [ ] Only explicitly allowed bytes reach an external Worker.
- [ ] Guardian and D9 real mutation remain unimplemented until their own gates pass.
- [ ] Full regression, architecture, compile, and exact-head CI are fresh before claiming a checkpoint.

