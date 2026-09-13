# Daily Supervisor Operation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Make the existing Codex Supervisor/Commander/Free Worker path a short, repeatable daily development operation without adding a new runtime state machine or scheduler.

**Architecture:** Keep Commander Plan and Supervisor metadata as the durable development boundary. Add only CLI adapters that delegate to existing review, rework, and integration methods; document the daily preflight, worker wait, review, recovery, and checkpoint rules in the existing entrypoint documents plus one compact runbook.

**Tech Stack:** Python 3.10/3.11, argparse, existing Commander/Supervisor APIs, pytest, GitHub Actions.

**Spec:** User instruction for daily Supervisor operation, `docs/CODEX_SUPERVISOR.md`, `docs/CODEX_COMMANDER.md`, and `docs/DEVFARM.md`.

## Global Constraints

- Do not add a new Production state machine, scheduler, daemon, retry engine, database, or Agent framework.
- Keep `STATIC_ONLY` as the default; `TRUSTED_HOST_EXEC` requires attempt-scoped explicit approval.
- Keep Human specification, budget, privacy, protected path, recovery, and Gate authority outside the Supervisor CLI.
- Keep Compression Service, OpenAI/Claude APIs, G6O1 runtime/live evidence, auto-merge, and auto-deploy disconnected.
- Use existing Commander `record_review_decision`, `reassign`, and `integrate_approved_worker` methods as the sole semantic authorities.

---

### Task 1: Expose daily Supervisor operator commands

**Files:**
- Modify: `scripts/devfarm_supervisor.py`
- Test: `tests/v2/test_devfarm_commander.py`

**Interfaces:**
- `review <run-id> <task-id> --attempt-id ... --decision ...` delegates to `record_review_decision()`.
- `rework <run-id> <task-id> --failure-evidence-ref ...` requires the durable current-attempt `REWORK` decision, then delegates to `rework_handoff()` and `reassign()`.
- `integrate <run-id> <task-id> --decision-id ... --target-checkout ... --target-ref ... --commit-message ...` delegates to `integrate_approved_worker()`.

- [x] Add parser options and bounded artifact-reference conversion.
- [x] Validate that rework uses the durable decision and exact correction.
- [x] Run the CLI tests with a temporary Git repository and Fake Worker.

### Task 2: Establish the shortest daily operation entrypoint

**Files:**
- Modify: `AGENTS.md`
- Create: `docs/CODEX_DAILY_DOGFOOD.md`
- Modify: `docs/CODEX_SUPERVISOR.md`
- Modify: `docs/CODEX_COMMANDER.md`

**Interfaces:**
- The short Human instruction is resolved from the repository’s current state and roadmap unless a Human Authority decision is required.
- The runbook uses `scripts/devfarm_supervisor.py run`, `review`, `rework`, and `integrate` as the daily operator surface.

- [ ] Add a compact `Default Daily Development Operation` section to `AGENTS.md`.
- [ ] Document preflight, unfinished-plan resume, task selection, worker wait, review, rework, integration, and checkpoint rules.
- [ ] Keep detailed contracts in the existing Supervisor/Commander documents rather than copying them into `AGENTS.md`.

### Task 3: Verify restart and CLI failure boundaries

**Files:**
- Test: `tests/v2/test_devfarm_supervisor.py`
- Test: `tests/v2/test_devfarm_commander.py`
- Modify: `docs/CODEX_DAILY_DOGFOOD.md`

- [ ] Cover wrong attempt, invalid decision, missing approval, dirty integration checkout, attempt limit, orphan recovery, and restart from the CLI/operator boundary.
- [ ] Record that `status`/`resume` never blindly replay an unknown external effect.

### Task 4: Run first daily Group D slice through the operation

**Files:**
- Create or modify only a narrow Group D Worker-owned path after current-state selection.
- Create: `spec/v2/evidence/daily-supervisor-20260913.json`
- Modify: `docs/CURRENT_STATE.md`
- Modify: `docs/V2_EXECUTION_PLAN.md`
- Modify: `spec/v2/TRACEABILITY.md`

- [ ] Reconfirm that the selected task is not G6O1/Compression/paid-provider/OS-sandbox frozen work.
- [ ] Use a non-overlapping narrow Worker manifest and the known qualified Gemini baseline first.
- [ ] Run `run` through Host Verification and explicit review; use the CLI integration helper only after approval.
- [ ] Record Worker/review/integration evidence without raw conversation or secrets.
- [ ] Run focused tests, `tests/v2`, architecture check, compileall, push, and exact-head CI.

## Exit Criteria

- `review`, `rework`, and `integrate` are usable from the CLI and have temporary-repository regression coverage.
- The daily runbook requires no copied long-form instruction for routine operation.
- Existing unfinished plans are resumed instead of duplicated.
- Worker waiting consumes no LLM polling and returns only at Codex action, Human decision, meaningful checkpoint, terminal blocker, or completion.
- A real Group D narrow task has passed the same Supervisor path, or a truthful external blocker is recorded without bypassing safety boundaries.
