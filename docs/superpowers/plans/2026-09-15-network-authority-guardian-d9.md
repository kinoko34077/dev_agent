# Network Authority, Guardian, and D9 Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the bounded network, authority, lease, Guardian, rollback, and adaptive-refinement gaps that precede D9 real mutation without weakening existing Host authority or UNKNOWN/reconciliation boundaries.

**Architecture:** Reuse the existing ProviderDispatcher/Resource admission, central protected-path policy, Process Coordination store/Guardian journal, RevisionPinnedRuntimeStore, and existing refinement policy. Add only typed diagnostics, freshness checks, proof records, and thin adapters. Provider HTTP remains Host/Agent-owned; no arbitrary proxy, second scheduler, retry engine, or D9 mutator is introduced.

**Tech Stack:** Python 3.10+, existing dataclasses/enums, stdlib Windows process adapters only behind static launch profiles, existing SQLite coordination store, pytest, architecture checker, compileall, and GitHub Actions exact-head checks.

**Spec:** `dev_agent 次段階統合実装指示書 — Network分離・Authority Hardening・Adaptive Refinement・Guardian常駐・D9 Real Repair移行` (2026-09-15 user instruction).

## Global Constraints

- Reconfirm branch, remote HEAD, dirty state, Current State, roadmaps, Gate, active DevFarm plans, evidence, and exact-head CI before each independently verifiable slice.
- Preserve `v2/bootstrap`, Task/Scheduler/Operation/Provider/Recovery/Host Verification/Authority boundaries, and D9 proposal-only status until every readiness prerequisite is independently evidenced and the Human explicitly releases a bounded mutation.
- Codex may own architecture, security, authority, recovery, process lifecycle, final review, and integration; narrow parsers, serializers, fixtures, tests, docs, and mechanical changes remain Worker candidates when the Host outbound path is available.
- Never retry or fail over an UNKNOWN external effect. Never log credentials, raw provider responses, raw conversations, or full sensitive payloads.
- Do not change the deferred GitHub bypass actor or `.dev_agent`/authority data-directory placement in this plan.
- Do not run repeated live Provider, Compression, paid-provider, MCP-wire, external Codex-session, OS-installation, or D9 mutation calls merely to manufacture evidence.

---

### Task 1: Establish current baseline and network ownership diagnostics

**Files:**
- Modify: `docs/CURRENT_STATE.md` only if the fresh baseline differs from the recorded implementation/evidence convention.
- Modify: `docs/V2_DETAILED_ROADMAP.md` only if the current next target does not describe N1/S1/S2/G6/R1/W1/G7/G8/D9-R.
- Test: `tests/v2/test_provider_transport_semantics.py` (create if no existing focused file covers the new classification).

**Interfaces:**
- Consume the existing `ProviderError` and `ProviderDispatcher`; do not add a provider-specific retry loop.
- Produce bounded diagnostic fields that distinguish `sandbox_network_denied`, `local_network_policy_denied`, and `provider_transport_failure` without exposing endpoint, credential, or response body.

- [x] Re-run `git status`, `git rev-parse`, Gate JSON validation, active-plan inspection, and latest CI listing; record the actual values in the work log before code changes.
- [x] Inspect every existing transport catch site and identify whether it has execution-scope evidence. Do not classify every Windows error 10013 as sandbox denial without an explicit scope/evidence input.
- [x] Write tests for explicit sandbox denial, explicit local-policy denial, provider transport failure, and ambiguous transport failure remaining reconciliation-required.
- [x] Run `python -m pytest tests/v2/test_provider_transport_semantics.py -q` and observe the new tests fail for the missing diagnostic contract.
- [x] Implement the smallest typed diagnostic projection at the provider boundary; keep `retryable`, `failover_safe`, and `requires_reconciliation` semantics compatible.
- [x] Re-run the focused tests and `python scripts/check_architecture.py`.

---

### Task 2: Close the central protected authority policy

**Files:**
- Modify: `src/dev_agent/security/protected_paths.py`.
- Test: `tests/v2/test_security_boundaries.py`.
- Test: the existing manifest/patch validation tests that exercise `allowed_files` and changed paths.

**Interfaces:**
- `is_protected_path()` remains the only central path decision consumed by Worker, Commander, Supervisor, and D9 policy.
- No caller-specific protected list may be added.

- [x] Add failing assertions for the coordination directory, self-improvement/self-repair/refinement authority, Supervisor, verification, and self-repair script paths named by the spec, including a new-file path under the protected coordination directory.
- [x] Add failing assertions for rename/new-file shadowing through the existing changed-path validator rather than only checking manifest headers.
- [x] Implement the minimal central path/prefix additions and keep non-authority neighboring files unprotected unless the responsibility rule requires it.
- [x] Run the focused security and manifest tests, then architecture and compileall.

---

### Task 3: Enforce lease freshness at authority-sensitive boundaries

**Files:**
- Modify: `src/dev_agent/coordination/service.py`.
- Modify: `src/dev_agent/coordination/guardian.py`.
- Test: `tests/v2/test_process_coordination_service.py`.
- Test: `tests/v2/test_process_coordination_guardian.py`.

**Interfaces:**
- Add one shared freshness predicate for `PeerRecord`; it must compare parsed timestamps at the authority check and use `lease_until > now`.
- Reuse that predicate from current-peer validation, Guardian sender/target evaluation, work claims, mailbox authority sends, and control requests.

- [x] Add red tests for valid lease, expired lease, exact equality at `lease_until`, stale generation with a valid-looking lease, expired sender, and expired target.
- [x] Run the focused tests and confirm the failures are caused by acceptance of expired peers, not test setup errors.
- [x] Implement the shared freshness check and pass one `now` through each authority decision so maintenance expiry is not a prerequisite for safety.
- [x] Re-run service/Guardian/coordination tests and confirm normal heartbeat/attach behavior remains unchanged.

---

### Task 4: Make Guardian process ownership crash-safe before OS residency

**Files:**
- Inspect and, only if the current abstraction lacks it, modify `src/dev_agent/coordination/guardian_process.py`.
- Modify: `src/dev_agent/coordination/guardian.py` only for ownership/reconciliation status wiring.
- Test: existing Guardian process tests plus a focused ownership test under `tests/v2/`.

**Interfaces:**
- Keep `LaunchProfile` static and reject arbitrary shell text.
- Add an injectable process-ownership adapter; Windows Job Object support may be implemented behind that adapter, while non-Windows/fake tests remain deterministic.
- `EXECUTING` interruption remains `UNKNOWN` and is never blindly replayed.

- [x] Write a failing fake-runtime test proving an unowned child cannot be treated as a current managed generation after Guardian restart.
- [x] Implement ownership metadata/adapter with a conservative Windows Job Object path when the platform API is available; otherwise return a bounded reconciliation-required result instead of guessing from PID.
- [x] Add tests for duplicate start, stale PID/creation identity, Guardian interruption, and unknown owner.
- [x] Run focused coordination/Guardian tests; do not claim deployed crash recovery or OS residency from these tests.

---

### Task 5: Add a durable RollbackProof boundary

**Files:**
- Modify: `src/dev_agent/intelligence/self_repair.py` or the smallest existing runtime rollback module that owns the D9 evidence contract.
- Modify: `src/dev_agent/coordination/runtime_release.py` only if proof generation needs an existing release result field.
- Test: `tests/v2/test_self_repair.py` and existing runtime release/rollback tests.

**Interfaces:**
- Produce an immutable, JSON-safe `RollbackProof` containing full revision SHA, existence, materialized release path/reference, clean verification, bounded health result, `verified_at`, and proof digest.
- Bind `RepairExecutionRequest`/candidate evaluation to the proof identity; retain backward-read compatibility for old proposal-only artifacts but fail closed for real execution.

- [x] Add red tests for missing proof, mismatched proof digest/revision, dirty release, failed health, and a valid proof.
- [x] Run the focused tests and observe failure before implementation.
- [x] Implement proof creation from `RevisionPinnedRuntimeStore` without mutating the official checkout or starting a runtime.
- [x] Re-run self-repair and runtime tests; keep D9 mutation disabled.

---

### Task 6: Connect one bounded Adaptive Refinement action

**Files:**
- Modify: `scripts/devfarm_refinement.py`.
- Reuse: `src/dev_agent/intelligence/refinement.py`, `src/dev_agent/intelligence/critic_adapter.py`, existing Supervisor REWORK/reassign APIs.
- Test: existing refinement/critic/Supervisor tests, plus one focused adapter test if missing.

**Interfaces:**
- Input: one validated failure packet and existing `RefinementPlan`.
- Output: one bounded next action (`CORRECT`, `CRITIQUE`, `REASSIGN_SAME_TIER`, `INCREASE_REASONING`, `ESCALATE_TIER`, `RECONCILE`, `HUMAN`, or `FAIL`) with no loop and no integration/approval authority.

- [x] Add a red test proving a format failure selects one correction action and does not raise thinking/tier.
- [x] Add a red semantic test proving one L1 Critic proposal can feed a bounded rework request without the Critic changing ownership or integrating.
- [x] Implement only the one-action Host adapter; persist round/usage references through existing attempt artifacts.
- [x] Run focused refinement tests and confirm successful first-pass paths make no extra model call.

---

### Task 7: Live production Worker evidence when the Host route is available

**Files:**
- Modify only the real narrow `src/` or `scripts/` target selected from the current roadmap.
- Create: one evidence JSON under `spec/v2/evidence/` containing task/attempt/provider/model/changed paths/verification/decision/integration/KPI references without raw payloads.
- Modify: `docs/CURRENT_STATE.md` only after independent Host verification and integration.

**Interfaces:**
- Use Commander Plan → existing Supervisor run → ReviewPacket → durable Codex decision → Host integration.
- Worker task must have non-overlapping ownership, a safe egress manifest, zero Codex direct implementation, and no protected responsibility.

- [ ] Select a real production helper/parser/serializer/CLI or bounded bugfix; do not count test-only or evidence-only documentation.
- [ ] If the current outbound boundary still returns `WinError 10013`, record it as transport/sandbox evidence and stop that live attempt without model escalation or repeated resend.
- [ ] If dispatch is available, run the bounded task, classify failure with Task 6, and use at most the existing bounded REWORK/reassign path.
- [ ] Integrate only after independent Host Verification and Codex ReviewDecision; record Worker KPIs and exact revision.

---

### Task 8: OS Guardian and real rolling/rollback readiness

**Files:**
- Add or modify only the existing Guardian operator entrypoint/config under `scripts/` and `src/dev_agent/coordination/`.
- Test: deterministic Windows-compatible operator tests; add read-only diagnostics for Service/Task Scheduler availability without installing system services automatically.
- Evidence: only after an actual bounded operator-approved OS run.

**Interfaces:**
- OS Service/Task Scheduler owns Guardian liveness only; Guardian owns process execution; Commander remains Task authority.
- Rolling restart uses pinned releases, drain/checkpoint, new-generation health before old stop, and RollbackProof.

- [x] Add a static launch-profile operator entrypoint for `guardian run`/health that does not accept arbitrary commands.
- [x] Add tests for startup configuration, generation fencing, health failure retaining the old generation, duplicate restart, stale request, mailbox replay, and rollback idempotency.
- [ ] Perform a real OS run only when the environment and explicit operator boundary permit it; otherwise leave status NOT VERIFIED with a concrete blocker.
- [ ] Do not mark G7/G8 verified from local fakes or foreground-only tests.

---

### Task 9: D9 readiness review, then bounded refactor stage

**Files:**
- Create: `spec/v2/evidence/d9-real-mutation-readiness-review-*.json` only when every prerequisite has fresh evidence.
- Modify: `docs/CURRENT_STATE.md`, `docs/V2_DETAILED_ROADMAP.md`, and `spec/v2/TRACEABILITY.md` only for verified status changes.
- Later refactor targets: select from actual dependency/ownership analysis; do not rewrite `OperationService`, `Controller`, or `ProviderDispatcher` by size alone.

**Interfaces:**
- D9 review must bind candidate, attempt, patch, manifest, Host Verification, ReviewDecision, Human approval, target revision, RollbackProof, pinned release, generation, health, and UNKNOWN behavior.
- The subsequent refactor must be split by responsibility/lifecycle/authority and preserve existing public boundaries with focused regression after each extraction.

- [ ] Re-read all prerequisite evidence and keep D9 real mutation locked if any item is missing.
- [ ] If explicitly released later, run one low-risk non-protected reversible repair through approval revalidation, Host integration, pinned runtime, drain, health, promotion, and rollback drill.
- [ ] After the current gates, profile/test import and dependency hotspots, then create a separate bounded refactor plan; remove duplicate private cross-module dependencies before splitting large core modules.
- [ ] For every refactor slice, run focused tests, architecture, compileall, full `tests/v2`, commit, push, and exact-head CI before the next slice.

## Verification and delivery checklist

- [ ] Every production behavior change has a focused red/green regression test.
- [ ] Expired peers cannot authorize work/control/mailbox operations, including exact boundary equality.
- [ ] Protected authority cannot be bypassed by manifest headers, rename, or new-file shadowing.
- [ ] UNKNOWN external/process outcomes remain reconciliation-only.
- [ ] `python -m pytest tests/v2 -q`, `python scripts/check_architecture.py`, and `python -m compileall -q src recovery scripts` pass at each applicable checkpoint.
- [ ] Documentation states the latest implementation commit before a documentation-sync commit and reports pushed remote HEAD separately.
- [ ] D9 is not called ready from artifacts alone; exact-head CI and independent evidence are required.
