# Multi-Free Provider Migration Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Prepare the current Phase 6 implementation and documentation for the accepted-candidate Multi-Free Provider requirements without implementing future provider, quota, intelligence, hedge, or AgentBackend features.

**Architecture:** Keep Controller -> ProviderDispatcher -> ProviderRegistry -> Concrete Provider as the preferred runtime path. Preserve the direct Controller provider path as an explicitly documented compatibility path, and split only provider-related tests into responsibility-focused modules through moves with no semantic edits. Store the new requirements as small chapter files plus an index so later work can load only the relevant chapter.

**Tech Stack:** Python 3.10/3.11, pytest, Markdown, JSON, GitHub Actions.

**Spec:** docs/requirements/multi-free-provider/00-index.md and its linked chapter files, derived from the user-provided Multi-Free Provider integration requirements.

## Global Constraints

- Keep G6O1 as external BLOCKED; do not make paid requests or invent paid evidence.
- Do not add quota_domain, new Providers, intelligence tiers, Router scoring, hedging, AgentBackend, MCP/API, Phase 7, or a large Controller rewrite.
- Preserve ExecutionContext, ToolRuntime.bound_to(), RuntimeState, AuditRecorder, ResourceLedger schema, and all existing test semantics.
- Preserve the evidence model: da74b3b is the tested code baseline and 74e54f9 is the documentation/evidence commit until the new verification commit is produced.
- Commit and push each independently verified batch to origin/v2/bootstrap.

### Task 1: Create chapterized requirements and indexes

**Files:**
- Create: docs/requirements/README.md
- Create: docs/requirements/multi-free-provider/00-index.md
- Create: docs/requirements/multi-free-provider/01-purpose-and-operating-model.md
- Create: docs/requirements/multi-free-provider/02-architecture-intelligence-and-task.md
- Create: docs/requirements/multi-free-provider/03-resource-quota-capability-and-routing.md
- Create: docs/requirements/multi-free-provider/04-provider-policy-privacy-and-hedging.md
- Create: docs/requirements/multi-free-provider/05-agent-backend-codex-mcp-and-authority.md
- Create: docs/requirements/multi-free-provider/06-workflow-evaluator-audit-metrics-and-survival.md
- Create: docs/requirements/multi-free-provider/07-phase-roadmap-invariants-and-target-state.md
- Test: Markdown link and heading validation commands.

**Interfaces:**
- Produces one index with chapter summaries and section mappings; each chapter is independently readable and states which items are future/non-goal.

- [x] Step 1: Create the top-level and chapter indexes with links only to chapter files and a short loading guide.
- [x] Step 2: Transcribe the supplied requirements into seven focused chapters without adding implementation requirements beyond the source.
- [x] Step 3: Validate that every source section 1-42 is mapped once and every index link resolves.
- [x] Step 4: Commit the requirements documentation.

### Task 2: Make the Provider runtime boundary explicit

**Files:**
- Modify: src/dev_agent/runtime/controller.py
- Modify: src/dev_agent/providers/dispatch.py
- Modify: docs/PHASE6_PLAN.md
- Modify: spec/v2/HARDENING_NEXT_PLAN.md
- Test: tests/v2/test_phase6_integration.py or a focused provider boundary test.

**Interfaces:**
- Consumes existing ProviderDispatcher, ProviderRegistry, ModelProvider, ResourceControlPlane, and ExecutionContext APIs.
- Produces explicit comments/docstrings and tests distinguishing the preferred dispatcher path from the compatibility direct-provider path; no behavior change.

- [x] Step 1: Add or adjust a failing boundary assertion that the dispatcher advertises the canonical provider path and the Controller direct branch is compatibility-only.
- [x] Step 2: Run the focused test and confirm the assertion fails.
- [x] Step 3: Add minimal documentation-level code markers/docstrings at the existing branch and dispatcher contract; do not rewrite dispatch logic.
- [x] Step 4: Run the focused test and confirm it passes.
- [x] Step 5: Update Phase 6 docs to state the boundary and list direct-provider behavior as compatibility.
- [x] Step 6: Commit the boundary clarification.

### Task 3: Split oversized provider-related integration tests without semantic edits

**Files:**
- Create: tests/v2/test_provider_dispatch.py
- Create: tests/v2/test_budget_dispatch.py
- Modify: tests/v2/test_phase6_integration.py
- Modify: tests/v2/test_integration_hardening.py only if a provider test is moved.

**Interfaces:**
- Produces the same pytest node IDs only where practical; moved tests must preserve bodies and assertions, while any unavoidable node-ID change is recorded in the changelog.

- [x] Step 1: Inventory provider, budget, and crash test functions and map each to exactly one destination file.
- [x] Step 2: Move only complete budget/resource-control test functions and required imports/helpers using a mechanical, reviewable patch; do not change assertions.
- [x] Step 3: Run the moved modules and compare collected test counts with the pre-move count.
- [x] Step 4: Run the complete v2 test suite.
- [x] Step 5: Commit the test organization change.

The split is intentionally light for this migration-prep boundary: three
resource/budget tests moved to test_budget_dispatch.py, while the existing
provider reconciliation and crash/replay tests remain together so that this
documentation-only transition does not create a large mechanical diff.

### Task 4: Synchronize Current State and verify the migration-prep boundary

**Files:**
- Modify: CHANGELOG.md
- Modify: spec/v2/GATE_STATUS.json only for truthful current-state synchronization.
- Modify: docs/PHASE6_PLAN.md
- Modify: spec/v2/HARDENING_NEXT_PLAN.md
- Test: full tests/v2, compileall, JSON validation, scripts/check_gate.py, scripts/check_head.py.

**Interfaces:**
- Produces an accurate statement that Phase 6 foundation is VERIFIED, G6O2-G6O6 are VERIFIED, G6O1 is BLOCKED_EXTERNAL, and the new requirements are accepted-candidate documentation for the next implementation stage.

- [x] Step 1: Re-read the chapter index and current Gate state.
- [x] Step 2: Update Current State without changing Gate status or inventing future evidence.
- [x] Step 3: Run full local verification and inspect all diffs.
- [x] Step 4: Commit and push the final migration-prep batch.
- [x] Step 5: Record remaining future work without starting it.

## Execution record

- Requirements documentation commit: 6bf82c8
- Provider boundary, test split, and Current State commit: 3e1cf3f
- Local verification: 253 passed, 1 skipped; compileall passed.
- Exact-head CI: v2-core run 34314830597 and v2 tests run 34314830596 both passed for 3e1cf3f6d871f61437993d8429eb287b6911fab9.
- G6O1 remains BLOCKED_EXTERNAL. No paid Provider request or future feature implementation was started.
