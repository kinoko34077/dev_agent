# Codex Dogfood Restart and DevFarm Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover the verified `v2/bootstrap` baseline and safely connect the existing isolated DevFarm to the concrete `CodexExecBackend`, stopping at HOST_VERIFIED without automatic integration, push, or merge.

**Architecture:** Keep `ModelProvider` and `AgentBackend` separate. The development-only runner will compose the existing Commander manifest, DevFarm worktree/authority checks, `CodexExecBackend`, and `HostVerificationRunner`; it will not create a new scheduler, state machine, worktree manager, or production runtime path. Codex edits only a task worktree derived from the manifest revision, while the host derives the authoritative patch from Git including untracked files and validates it before any test execution.

**Tech Stack:** Python 3.10/3.11, `pytest`, `git`, existing SQLite/JSON DevFarm artifacts, Windows-safe subprocess/process-tree handling, and the installed Codex CLI when its authenticated read-only smoke test is available.

**Spec:** User-provided `dev_agent v2 — Codex再開・Dogfood移行 実装指示書`; repository contracts in `AGENTS.md`, `docs/DEVFARM.md`, `docs/CODEX_COMMANDER.md`, `spec/v2/05_api_spec.md`, and `spec/v2/06_implementation_spec.md`.

## Global Constraints

- Work only on `v2/bootstrap`; `main` is the frozen v1 line.
- Do not use `rm -rf`, recursive `Remove-Item`, `git clean`, `git reset --hard`, `shutil.rmtree`, or any repository-root deletion.
- Preserve existing Controller, Queue, StateStore, Provider, Resource, Recovery, authority, lease/fencing, effect-intent, and reconciliation boundaries.
- Codex-owned work covers process safety, isolation, authority, cross-cutting integration, review, and final regression; narrow tests, fixtures, parsers, and docs are Worker candidates but cannot own protected authority changes.
- Every external proposal remains subject to deterministic path/protected/scope validation and Host Verification; unsandboxed host execution is never the highest-trust unattended mode.
- Dogfood v0 stops at `HOST_VERIFIED`; it never auto-applies to `v2/bootstrap`, auto-merges, auto-pushes, changes Gate status, or changes budget/credential/recovery authority.
- A real Codex CLI result is evidence only after independent host Git inspection and verification; model/self-reported files or tests are not authority.

---

### Task 1: Reconcile incident baseline and create the tracked execution record

**Files:**
- Create: `docs/superpowers/plans/2026-09-12-codex-dogfood-restart.md`
- Inspect: `AGENTS.md`, `docs/CURRENT_STATE.md`, `docs/SYSTEM_MAP.md`, `docs/V2_EXECUTION_PLAN.md`, `docs/DEVFARM.md`, `docs/CODEX_COMMANDER.md`, `spec/v2/`, `tests/v2/`
- Test: `tests/v2/` and `scripts/check_architecture.py`

**Interfaces:**
- Consumes: the checked-out `v2/bootstrap` repository and `origin/v2/bootstrap` ref.
- Produces: a verified branch/status/HEAD record and a clean baseline report; no production code change.

- [ ] **Step 1: Confirm repository identity without mutation**

Run:

```powershell
git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" status --short --branch
git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" branch --show-current
git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" rev-parse HEAD
git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" remote -v
```

Expected: repository exists, branch is `v2/bootstrap`, and no accidental cleanup is required.

- [ ] **Step 2: Verify baseline checks**

Run:

```powershell
python -m pytest tests/v2 -q
python scripts/check_architecture.py
python -m compileall -q src recovery scripts
```

Record exact counts and distinguish local evidence from unavailable GitHub authentication.

- [ ] **Step 3: Inspect the current Codex/DevFarm contracts**

Run:

```powershell
rg -n "CodexExecBackend|_remove_directory|shutil\.rmtree|HostVerificationRunner|validate_manifest|git diff|untracked|attempt" src scripts tests/v2
```

Use the existing public helpers and tests as the implementation boundary; do not infer missing APIs from the instruction alone.

- [ ] **Step 4: Commit only the plan when the baseline is clean**

```powershell
git add docs/superpowers/plans/2026-09-12-codex-dogfood-restart.md
git commit -m "docs: plan safe Codex dogfood restart"
```

### Task 2: Replace Codex temporary-directory hard delete with safe disposal

**Files:**
- Modify: `src/dev_agent/backends/codex_exec.py`
- Test: `tests/v2/test_codex_exec_backend.py`

**Interfaces:**
- Consumes: explicit Codex session temporary directory paths.
- Produces: `_remove_directory(path)` behavior that validates a narrow temporary target and moves it to a unique quarantine sibling instead of recursively deleting it.

- [ ] **Step 1: Add the failing disposal test**

The test must create a temporary directory containing a marker, call `_remove_directory`, assert the original path is gone, assert exactly one explicitly named quarantine directory contains the marker, and assert passing the parent/root target raises the existing safety error. It must not call a recursive delete helper in test cleanup.

- [ ] **Step 2: Run the focused test and observe the expected failure**

```powershell
python -m pytest tests/v2/test_codex_exec_backend.py -q
```

Expected failure: the current helper removes the directory or lacks the required safety rejection.

- [ ] **Step 3: Implement the narrow quarantine operation**

Use `Path.resolve()` and explicit checks that the candidate exists, is a directory, is not the supplied root/parent, user profile, Documents ancestor, drive root, or current repository. Generate a unique sibling quarantine name and use a single-directory rename/move. Do not add a generic cleanup function and do not use `shutil.rmtree`.

- [ ] **Step 4: Run the focused and existing backend tests**

```powershell
python -m pytest tests/v2/test_codex_exec_backend.py -q
```

- [ ] **Step 5: Commit the isolated hardening change**

```powershell
git add src/dev_agent/backends/codex_exec.py tests/v2/<codex-test-file>.py
git commit -m "fix: make Codex temporary disposal reversible"
```

### Task 3: Unify Codex subprocess I/O failure handling

**Files:**
- Modify: `src/dev_agent/backends/codex_exec.py`
- Test: existing CodexExecBackend subprocess tests; add regression coverage in the same test module

**Interfaces:**
- Consumes: stdin writer and stdout/stderr drain threads.
- Produces: every I/O failure terminates the process tree, waits only for a bounded stop confirmation, closes stdin in `finally`, and returns UNKNOWN metadata distinguishing `confirmed_stopped` from `unconfirmed`.

- [ ] **Step 1: Add one failing test for stdout/stderr failure and one for stdin failure**

Use the existing fake process wrappers. Inject an exception from each pipe operation, assert `terminate_process_tree` is requested, assert stdin close is attempted, and assert the result is UNKNOWN rather than FAILED. Do not assert only that a mock was called; inspect the returned status and stop metadata.

- [ ] **Step 2: Run only the new tests**

```powershell
python -m pytest tests/v2/test_codex_exec_backend.py -k "io_failure or stdin_failure" -q
```

Expected: failure on at least one unhandled I/O path before production changes.

- [ ] **Step 3: Extract one local failure-to-termination helper**

The helper must record the first I/O error, call the existing process-tree terminator once, wait with a monotonic deadline, and expose stop confirmation to the existing result normalization. Do not introduce a new state machine or executor.

- [ ] **Step 4: Run the complete Codex backend test cluster**

```powershell
python -m pytest tests/v2/test_codex_exec_backend.py tests/v2/test_agent_backend_dispatcher.py -q
```

- [ ] **Step 5: Commit the process-safety change**

```powershell
git add src/dev_agent/backends/codex_exec.py tests/v2/test_codex_exec_backend.py
git commit -m "fix: normalize Codex subprocess I/O failure shutdown"
```

### Task 4: Verify the real Codex CLI without exposing parent credentials

**Files:**
- Modify: none initially; only update `docs/CURRENT_STATE.md` after evidence is collected.
- Test artifact: a temporary fixture outside the repository, retained or quarantined safely according to Task 2.

**Interfaces:**
- Consumes: installed `codex` binary and the existing CodexExecBackend command/environment boundary.
- Produces: a truthful smoke result: runnable/authenticated, runnable but unauthenticated, or unavailable; never a fabricated success.

- [ ] **Step 1: Inspect binary and help output**

```powershell
Get-Command codex -ErrorAction SilentlyContinue
codex --version
codex exec --help
```

- [ ] **Step 2: Run a read-only bounded smoke in a tiny non-repository directory**

The prompt must request inspection only, use `--json`, use the existing temporary HOME/config isolation, and have a short deadline. Do not pass the parent environment wholesale and do not include provider API keys.

- [ ] **Step 3: Compare the observed CLI behavior with `CodexExecBackend`**

If the CLI flags or authentication boundary differ, add a focused adapter test or a documented external blocker; do not guess at authentication changes.

- [ ] **Step 4: Record only observed evidence**

Update the owning Current State section with the binary/version/auth result and the exact limitation. Leave OS sandbox and branch protection as external/backlog items if not available.

### Task 5: Add DevFarm Codex one-attempt runner with Git-authoritative diff

**Files:**
- Create: `scripts/devfarm_codex.py`
- Modify: only existing public DevFarm/Commander module exports if needed
- Test: `tests/v2/test_devfarm_codex.py`

**Interfaces:**
- Consumes: a validated Commander/DevFarm manifest, `CodexExecBackend`, and the manifest base revision.
- Produces: `CodexDevFarmAttempt` containing attempt identity, process/session result, authoritative changed paths, patch digest, and verification state; default host trust is `STATIC_ONLY`.

- [ ] **Step 1: Add failing tests for tracked and untracked changes**

Create a real temporary Git repository fixture. Run a fake injected backend that modifies one tracked file and creates one untracked file. Assert the runner discovers both without changing the official checkout or leaving staged changes in the fixture’s normal index. Add tests that an out-of-scope or protected path is rejected before Host Verification.

- [ ] **Step 2: Run the new tests and observe RED**

```powershell
python -m pytest tests/v2/test_devfarm_codex.py -q
```

Expected failure: the runner module/API is absent.

- [ ] **Step 3: Implement manifest-to-backend request composition**

Build the objective from `task_id`, objective, base revision, allowed/read/forbidden files, requirements, acceptance, test commands, and protected restrictions. Create only the existing isolated worktree for the attempt. Keep `workspace_id` and allowed paths explicit in `AgentBackendScope`.

- [ ] **Step 4: Implement temporary-index Git inspection**

Resolve the base revision to a commit, create a temporary `GIT_INDEX_FILE` outside the official index, run `git read-tree <base>`, `git add --all` in the isolated worktree, and obtain `git diff --cached --binary <base>`. Parse status/path output and validate the complete patch, including untracked files, through existing path/protected/symlink checks. Remove only the explicitly created temporary index file after the operation.

- [ ] **Step 5: Produce immutable attempt evidence**

Write through the existing attempt-artifact helpers with a unique attempt ID. Store the backend result as non-authoritative metadata; store changed files, patch digest, and scope decision from host inspection. Never overwrite an attempt artifact.

- [ ] **Step 6: Run the focused DevFarm Codex cluster**

```powershell
python -m pytest tests/v2/test_devfarm_codex.py tests/v2/test_devfarm_worker.py tests/v2/test_devfarm_commander.py -q
```

- [ ] **Step 7: Commit the one-attempt integration**

```powershell
git add scripts/devfarm_codex.py tests/v2/test_devfarm_codex.py
git commit -m "feat: connect Codex exec to isolated DevFarm attempts"
```

### Task 6: Connect existing Host Verification and bounded failure feedback

**Files:**
- Modify: `scripts/devfarm_codex.py`
- Test: `tests/v2/test_devfarm_codex.py`

**Interfaces:**
- Consumes: Task 5 authoritative attempt artifacts and existing `HostVerificationRunner`.
- Produces: static validation by default; explicit per-attempt `TRUSTED_HOST_EXEC` only when supplied; failure evidence for the next bounded attempt; no automatic integration.

- [ ] **Step 1: Add failing trust-level tests**

Assert that an external/Codex patch with default `STATIC_ONLY` never launches pytest and is not accepted. Assert an explicit attempt-scoped trusted execution flag permits only the declared strict command set. Assert a failed verification creates bounded failure evidence without changing official branch state.

- [ ] **Step 2: Run the tests to verify RED**

```powershell
python -m pytest tests/v2/test_devfarm_codex.py -k "static_only or trusted_host or feedback" -q
```

- [ ] **Step 3: Compose existing verification without a parallel state machine**

Call the existing runner only after Git scope authority passes. Reuse its sanitized environment, temporary HOME, output bounds, and process-tree timeout. For default static mode, run no patched-code command. For an explicit trusted attempt, record trust level, test spec digest, verified commands, and bounded output in the existing immutable evidence shape.

- [ ] **Step 4: Add finite retry/reassignment input**

Use the existing Commander attempt count and result artifact references. Propagate only failed command, exit code, failed names, bounded sanitized output, changed files, scope failure, and prior diff summary. Reject a new attempt when `max_attempts` is reached or when the prior external outcome is UNKNOWN.

- [ ] **Step 5: Run the complete affected suite**

```powershell
python -m pytest tests/v2/test_devfarm_codex.py tests/v2/test_devfarm_worker.py tests/v2/test_devfarm_commander.py tests/v2/test_agent_backend_dispatcher.py -q
```

- [ ] **Step 6: Commit the verification boundary**

```powershell
git add scripts/devfarm_codex.py tests/v2/test_devfarm_codex.py
git commit -m "feat: verify Codex DevFarm attempts with bounded feedback"
```

### Task 7: Execute one real Codex dogfood attempt, if the smoke gate permits

**Files:**
- Create: a Commander plan and manifest under ignored `.devfarm/` only
- Modify: none on official branch during the attempt
- Evidence: immutable `.devfarm/` attempt/result/verification/metrics artifacts

**Interfaces:**
- Consumes: Task 5/6 runner, the real CLI smoke result, and a narrow non-protected task.
- Produces: `HOST_VERIFIED` evidence or a truthful bounded blocker; never `INTEGRATED`.

- [ ] **Step 1: Create a parent plan with explicit delegation fields**

Record one Codex-owned task (`process/integration/security` reason) and at least one narrow Worker-eligible task. Do not overlap file ownership. The task must change a small non-protected fixture/test/documentation file and use an existing trusted host test target.

- [ ] **Step 2: Dispatch at most the configured bounded attempts**

Use the real `CodexExecBackend` in an isolated worktree. Do not send the repository root, credentials, `.git`, `.devfarm`, or protected authority paths.

- [ ] **Step 3: Reacquire Git evidence after process exit**

Validate tracked, added, deleted, renamed, untracked, protected, symlink, base revision, and diff-size conditions independently from Codex output.

- [ ] **Step 4: Run Host Verification only at the approved trust level**

Record `provider/backend`, model or CLI version, attempt, elapsed duration, changed files, accepted/rejected, host tests, and bounded failure evidence. If the CLI is unauthenticated or missing, record an external blocker and stop this task without treating a fake result as real dogfood.

- [ ] **Step 5: Confirm no official branch or remote mutation**

```powershell
git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" status --short --branch
git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" diff --exit-code
git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" log -1 --oneline
```

- [ ] **Step 6: Commit only code/docs evidence after independent review**

Do not stage ignored `.devfarm/` artifacts unless the repository policy explicitly tracks a compact evidence file. If code and tests pass, commit the implementation; leave the dogfood worktree/result artifacts ignored and immutable on disk.

### Task 8: Final regression, docs, and delivery decision

**Files:**
- Modify: `docs/CURRENT_STATE.md`, `docs/V2_EXECUTION_PLAN.md`, and relevant `spec/v2/` only with observed implementation/evidence changes
- Inspect: `.github/workflows/`, `spec/v2/GATE_STATUS.json`

**Interfaces:**
- Consumes: focused test results, full regression, architecture/compile checks, and any authenticated GitHub Actions evidence.
- Produces: truthful current-state record, clean commit, and push only after all local gates pass and no unreviewed user changes are present.

- [ ] **Step 1: Run the final local gate**

```powershell
python -m pytest tests/v2 -q
python scripts/check_architecture.py
python -m compileall -q src recovery scripts
```

- [ ] **Step 2: Verify the exact local HEAD and worktree**

```powershell
git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" status --short --branch
git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" rev-parse HEAD
git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" diff --check
```

- [ ] **Step 3: Update docs without promoting unsupported gates**

Record the actual Codex CLI state, exact test count, Host Verification trust level, dogfood outcome, and any external GitHub/auth/sandbox blocker. Do not claim OS sandbox, automatic integration, or paid-provider completion from this batch.

- [ ] **Step 4: Push only the verified implementation commits**

```powershell
git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" push origin v2/bootstrap
```

After pushing, query exact-head Actions when authenticated. If GitHub auth is unavailable, report that external verification remains pending rather than substituting local results.

## Plan self-review

- The plan keeps the official branch untouched during Codex execution and treats Git inspection as the patch authority.
- It covers safe disposal, all subprocess I/O failure paths, real CLI smoke, isolated worktree editing, untracked detection, strict Host Verification, finite feedback, and final regression.
- No new production scheduler, state machine, Agent framework, MCP layer, or OS sandbox is introduced.
- The real CLI step is explicitly conditional on observed availability/authentication; absence is recorded as an external blocker rather than guessed around.
