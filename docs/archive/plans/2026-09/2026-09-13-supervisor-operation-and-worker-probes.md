# Supervisor Operation and Worker Probes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Codex-supervised Commander run return promptly for Codex actions, wait without LLM polling for Worker work, and produce bounded evidence from staged free-Worker capability probes and real Dogfood.

**Architecture:** Keep `advance()` as one bounded pass and use the existing `run_until_intervention()` as the only blocking composition.  A small predicate over existing Supervisor metadata and Commander task ownership identifies Codex actions; no new scheduler or state machine is added.  Fixed development-only probes reuse `ModelRequest`, `ProviderFactory`, and existing Worker eligibility, while real edits continue through DevFarm, Host Verification, ReviewDecision, and deterministic integration.

**Tech Stack:** Python 3.10/3.11, stdlib `dataclasses`/`json`/`hashlib`/`argparse`, pytest, existing DevFarm/Commander/ProviderFactory, Git, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-09-13-supervisor-operation-and-worker-probes-design.md`

## Global Constraints

- Preserve existing Commander, DevFarm, Host Verification, ReviewPacket, ReviewDecision, Task, Scheduler, Budget, Approval, Privacy, Recovery, and protected-path authority.
- Do not add a production scheduler, a parallel state machine, a retry framework, Compression Service connectivity, paid-provider qualification, G6O1-SIM runtime E2E, automatic push/merge/deploy, or an OS sandbox.
- `STATIC_ONLY` remains the default; `TRUSTED_HOST_EXEC` requires explicit approval for one concrete attempt.
- Result-less expired `DISPATCHED` work remains orphaned and reconciliation-required; never blindly retry it.
- Worker output is not authority.  Host validates scope, patch bytes, verification, and Git integration.
- Probe success is observation only and does not grant qualification, activation, routing, budget, or authority.
- The G6O1 record remains not verified; its practical paid-provider work is frozen and non-blocking only in the companion roadmap metadata.

---

### Task 1: Separate Codex actions from Worker wait

**Files:**
- Modify: `scripts/devfarm_supervisor.py:run_until_intervention()` and the metadata classification in `advance()`
- Test: `tests/v2/test_devfarm_supervisor.py`
- Test: `tests/v2/test_devfarm_commander.py` for the existing review/integration regression
- Modify: `docs/requirements/model-handoff/04-codex-supervised-dogfood.md`

**Interfaces:**
- Consumes existing `SupervisorStep.status`, `SupervisorStep.next_action`, Commander task `owner`/`status`, and `normalize_supervisor_metadata()`.
- Produces no new status enum.  The private classification may be named `_requires_codex_action(plan, step)` and must return `bool` only.
- The wait-budget boundary returns existing `status="WAITING_FOR_WORKER"` with `next_action="wait_budget_exhausted"` and a deduplicated `SUPERVISOR_WAIT_BUDGET_EXHAUSTED` wake event.

- [ ] **Step 1: Write failing regression tests for immediate Codex return.**

Add tests that monkeypatch `advance()` with `dataclasses.replace()` and make `sleep_fn` raise if called:

```python
@pytest.mark.parametrize(
    ("status", "next_action"),
    [
        ("REVIEWING", "review_host_verified"),
        ("INTEGRATING", "integrate_verified_worker"),
        ("ACTIVE", "rework_worker"),
    ],
)
def test_run_returns_immediately_for_codex_action(tmp_path, status, next_action):
    create_plan(tmp_path, _plan())
    runner = CodexSupervisedCommanderRun(tmp_path, "supervisor-test-001")
    runner.create()
    baseline = runner.status()
    runner.advance = lambda **_kwargs: replace(baseline, status=status, next_action=next_action)
    step = runner.run_until_intervention(
        providers={},
        max_wait_seconds=60,
        sleep_fn=lambda _seconds: (_ for _ in ()).throw(AssertionError("Codex action must not sleep")),
    )
    assert (step.status, step.next_action) == (status, next_action)
```

Add a plan-level test for a `READY` task whose `owner` is `codex`; `advance()` must expose `next_action="execute_codex_task"` and `run_until_intervention()` must return without sleep.

- [ ] **Step 2: Run the focused tests and verify the expected failures.**

Run:

```powershell
python -m pytest tests/v2/test_devfarm_supervisor.py -q
```

Expected failure: the current run sleeps for `INTEGRATING`/`ACTIVE` actions and `advance()` does not expose a Codex-owned ready task action.

- [ ] **Step 3: Write the wait-budget regression test.**

Use a monotonic sequence that reaches the caller budget after one waiting pass.  Assert that the returned step is not `HUMAN_DECISION_REQUIRED`, that `next_action == "wait_budget_exhausted"`, and that exactly one `SUPERVISOR_WAIT_BUDGET_EXHAUSTED` wake exists.  Repeat the call with the same metadata and assert the wake is deduplicated.

- [ ] **Step 4: Implement the minimum classification change.**

In `advance()`, classify Codex-owned `PLANNED`/`READY` work before the generic `ACTIVE/advance` fallback.  Use `execute_codex_task` as the durable next action.  In `run_until_intervention()`, check the existing review/integration/rework/Codex-owned actions before constructing a sleep interval.  On `max_wait_seconds` and overall deadline expiry, persist the bounded-wait action/event without changing the result to Human decision.

- [ ] **Step 5: Run focused and compatibility tests.**

Run:

```powershell
python -m pytest tests/v2/test_devfarm_supervisor.py tests/v2/test_devfarm_commander.py -q
```

Verify existing review, explicit approval, integration, rework, orphan recovery, and terminal-state tests remain green.

- [ ] **Step 6: Synchronize the requirement wording and commit.**

Document that only Worker-pending states sleep, while Codex action states return to the Codex caller.  Commit only these code, test, and requirement changes:

```powershell
git add scripts/devfarm_supervisor.py tests/v2/test_devfarm_supervisor.py tests/v2/test_devfarm_commander.py docs/requirements/model-handoff/04-codex-supervised-dogfood.md
git commit -m "fix: return supervisor for codex actions"
```

### Task 2: Add fixed capability probes and improve Worker patch guidance

**Files:**
- Create: `scripts/devfarm_capability_probe.py`
- Modify: `scripts/devfarm_worker.py:_prompt()` only for measured unified-diff output failures
- Create: `tests/v2/test_devfarm_capability_probe.py`
- Modify: `tests/v2/test_devfarm_manifest.py` for the Worker prompt contract regression
- Modify: `docs/DEVFARM.md` with the probe ladder and failure classification

**Interfaces:**
- `ProbeSpec(level: str, name: str, prompt: str, checker: str)` is a fixed internal catalog record; callers select a finite named level, not an arbitrary prompt.
- `run_probe(provider: ModelProvider, spec: ProbeSpec, *, now: datetime | None = None) -> dict[str, Any]` returns only bounded evidence fields: provider/model identity, level, request digest, response digest, output size, status, failure category, and short issue.
- `probe_specs(levels: Sequence[str] | None = None) -> tuple[ProbeSpec, ...]` returns the fixed ordered ladder and rejects unknown or duplicate levels.
- The CLI accepts `--provider`, `--model`, `--level` repeated at most six times, `--timeout-seconds`, and `--root`; it has no arbitrary prompt, system prompt, tool, temperature, or provider-option argument.

- [ ] **Step 1: Add failing tests for the fixed ladder and bounded result.**

Use an existing test-double provider and assert the catalog order and exact levels:

```python
def test_probe_catalog_is_fixed_and_bounded():
    specs = probe_specs()
    assert [item.level for item in specs] == ["P0", "P1", "P2", "P3", "P4", "P5"]
    assert len(specs) == 6
    with pytest.raises(ProbeError):
        probe_specs(["P0", "P0"])
    with pytest.raises(ProbeError):
        probe_specs(["P9"])
```

Add one test per response category: exact `A` passes P0; a non-matching response is `model_output_invalid`; `ProviderError` is `provider_error` with its category preserved; an identity mismatch is `provider_contract_mismatch`.  Assert that no raw response text or credential field occurs in the returned evidence.

- [ ] **Step 2: Run the probe tests and verify they fail because the probe module is absent.**

Run:

```powershell
python -m pytest tests/v2/test_devfarm_capability_probe.py -q
```

Expected failure: import or symbol failure for the new fixed probe catalog.

- [ ] **Step 3: Implement the six fixed probe specifications.**

Use these exact prompts and bounded check intent:

| Level | Prompt | Check |
| --- | --- | --- |
| P0 | `SENT 'A' ONLY` | response is exactly `A` after outer whitespace removal |
| P1 | `3+5=?` | response contains the standalone result `8` |
| P2 | `Pythonのprintfの使い方` | response explains Python `print(` without requiring an unrelated API |
| P3 | `numpyでの行列変換の依頼` | response contains a bounded matrix operation such as `@`, `dot`, or `matmul` and does not contain an empty response |
| P4 | `単純作業の実行` | response contains a finite ordered action/result description and remains within the output bound |
| P5 | `複数判断の放任作業` | response identifies multiple bounded choices and a result without claiming authority changes |

Construct each request with the existing `ModelRequest`, a fixed system instruction, a stable probe task id, and `max_output_tokens` no greater than 512.  Do not persist raw output; retain SHA-256, character count, and a sanitized bounded issue.

- [ ] **Step 4: Implement explicit failure separation and CLI bounds.**

Catch `ProviderError` before broad exceptions, classify adapter/configuration errors separately from output mismatch, preserve the admitted provider/model identity, and write one uniquely named JSON evidence artifact below `.devfarm/probes/`.  Reject more than six selected levels, arbitrary level names, invalid timeouts, and non-active/unqualified bindings through the existing `DevFarmActivationPolicy`/`ProviderFactory` path.

- [ ] **Step 5: Improve the Worker prompt only where prior evidence supports it.**

Retain literal unified diff as the output contract.  Add a compact one-hunk checklist: obtain the exact base file content, copy exact `diff --git` paths, make hunk line numbers match the supplied content, keep `changed_files` consistent with the patch, and return one final newline.  Do not change `validate_patch()`, hunk validation, protected-path checks, or scope authority.

- [ ] **Step 6: Run focused probe and Worker contract tests.**

Run:

```powershell
python -m pytest tests/v2/test_devfarm_capability_probe.py tests/v2/test_devfarm.py tests/v2/test_devfarm_patch_validation.py -q
```

### Task 3: Record G6O1 as frozen and non-blocking without false verification

**Files:**
- Create: `spec/v2/G6O1_DEFERRED.md`
- Modify: `docs/CURRENT_STATE.md` at the current-state section
- Modify: `docs/V2_EXECUTION_PLAN.md` in the four-axis current-state section
- Modify: `spec/v2/TRACEABILITY.md` at the G6O1 mapping
- Modify: `docs/requirements/model-handoff/03-compression-and-g6o1.md` only to link the freeze companion

**Interfaces:**
- `spec/v2/GATE_STATUS.json` remains `G6O1.status="BLOCKED"` and `actionable=false`; no evidence is upgraded.
- The companion document records `verification=NOT VERIFIED`, `deferred=true`, `roadmap_blocking=false`, `frozen_at`, `frozen_by=Human decision`, preserved scope, and resume procedure.

- [ ] **Step 1: Add a documentation test/check expectation.**

Use a read-only script assertion in the focused documentation check or test to require the new file to contain all of `NOT VERIFIED`, `roadmap_blocking`, `G6O1-SIM`, `G6O1-LIVE`, `resume trigger`, and the canonical Gate reference.  Assert that `GATE_STATUS.json` still has G6O1 status `BLOCKED`.

- [ ] **Step 2: Create the freeze companion with preserved acceptance criteria.**

State that real paid-provider qualification, worst-case billing, deployment-owned paid budget, and simulated-paid runtime E2E are deferred/frozen.  Link the original requirement and Gate records; list explicit resume triggers and the six-step resume procedure.

- [ ] **Step 3: Update Current State, roadmap, traceability, and requirement index.**

Separate evidence status from roadmap blocking.  Record that Supervisor intervention work and Worker probes are the active development target, while G6O1 remains external and unverified.  Do not write `G6O1 PASS`, `VERIFIED`, or equivalent wording.

- [ ] **Step 4: Run document checks and commit.**

Run:

```powershell
python -m pytest tests/v2/test_gate_checker.py -q
git diff --check
git add spec/v2/G6O1_DEFERRED.md docs/CURRENT_STATE.md docs/V2_EXECUTION_PLAN.md spec/v2/TRACEABILITY.md docs/requirements/model-handoff/03-compression-and-g6o1.md
git commit -m "docs: freeze g6o1 without blocking development"
```

### Task 4: Execute the first real Free Worker Dogfood

**Files:**
- Create/modify: ignored `.devfarm/` plan, manifest, result, verification, and evidence artifacts only
- Do not modify: `v2/bootstrap` source checkout before Host integration
- Evidence: `spec/v2/evidence/supervisor-dogfood-20260913.json`

**Interfaces:**
- Use `create_plan()`, `CodexSupervisedCommanderRun.create()`, `run_until_intervention()`, `record_review_decision()`, `integrate_approved_worker()`, and existing `reassign()` only.
- The first task is one narrow, one-file, non-protected documentation change with an existing trusted verification command.  The first provider order is exact qualified Gemini L1, then an already qualified Cloudflare/OpenRouter L1 only if the primary is unavailable; no blind retry follows an unknown external outcome.

- [ ] **Step 1: Verify eligibility immediately before dispatch.**

Use the existing activation/qualification path to confirm exact provider, binding, model, current qualification, billing admission, and operator activation.  Record the result without storing credentials.  If the API or request shape fails, classify it as provider configuration/contract failure and stop that binding; do not call it a model failure.

- [ ] **Step 2: Dispatch one explicit `TRUSTED_HOST_EXEC` attempt.**

Run the existing Supervisor/Commander path with `operator_approved=True`.  Confirm the default path remains `STATIC_ONLY` in a separate test.  The Worker gets only its isolated manifest and allowed files.

- [ ] **Step 3: Verify the Host-side evidence chain.**

Require actual changed paths, patch digest, immutable attempt artifact, Host Verification result, independent trusted target, ReviewPacket, and `HOST_VERIFIED`.  Reject empty patch, malformed hunk, scope violation, protected-path change, and untracked omission; do not loosen validation for a real model.

- [ ] **Step 4: Record a durable Codex approval and integrate deterministically.**

Use `record_review_decision(..., decision="APPROVE_INTEGRATION")`, then `integrate_approved_worker()` with a clean target checkout.  Confirm the commit revision, verified patch digest, source attempt, and Commander `INTEGRATED` state.  Do not push, merge, alter Gate status, or deploy.

- [ ] **Step 5: Save compact evidence and classify failures.**

Record provider/binding/model, task, attempt, elapsed time, supplied usage, changed files, patch digest, Host tests, decision, integration revision, and failure category in `spec/v2/evidence/supervisor-dogfood-20260913.json`.  Never record API keys or raw Worker conversation.

### Task 5: Execute REWORK and dependency Dogfood

**Files:**
- Use: existing `.devfarm/` plan/manifest/attempt artifacts
- Evidence: append to `spec/v2/evidence/supervisor-dogfood-20260913.json`
- Test: `tests/v2/test_devfarm_commander.py` only if the real run exposes a missing regression

- [ ] **Step 1: Produce one truthful REWORK decision.**

Use a review finding that is not a safety bypass, such as an incomplete required documentation sentence or acceptance item.  Persist `ReviewDecision.REWORK` with `authority_source=review_decision`, required correction, evidence references, and the original attempt id.

- [ ] **Step 2: Reassign through the immutable rework manifest.**

Call `rework_handoff()` and `reassign()`; assert the new manifest differs from the original, carries only the correction delta/reference, has a new attempt identity, and does not mutate the old verified attempt.

- [ ] **Step 3: Re-run the next Worker attempt and verify it.**

Use the same exact eligibility checks and bounded trust approval.  Require a new Host Verification artifact and a second durable ReviewPacket/ReviewDecision before integration.

- [ ] **Step 4: Run two dependency-linked tasks.**

Task A must integrate before Task B becomes READY.  Task B’s new manifest must carry A’s `integration_revision` as its base.  Use non-overlapping ownership and independent attempts.  Record release timing and both integration revisions.

- [ ] **Step 5: Keep Supervisor continuation semantics observable.**

During Worker execution, do not call a second LLM or manually resume from the Human-facing path.  Verify that Worker-pending work sleeps in the bounded run, while review, rework, integration, and Codex-owned work return as Codex actions.

### Task 6: Final validation and synchronization

**Files:**
- Modify: `docs/CURRENT_STATE.md` with exact implementation commit, fresh local results, dogfood evidence, unimplemented scope, and next target
- Modify: `docs/V2_EXECUTION_PLAN.md` Implementation Frontier and Next Development Target
- Modify: `docs/CODEX_SUPERVISOR.md` if the final return/wait wording differs from the current contract
- Modify: `spec/v2/TRACEABILITY.md` for implemented Supervisor/probe evidence

- [ ] **Step 1: Run focused suites after all implementation changes.**

```powershell
python -m pytest tests/v2/test_devfarm_supervisor.py tests/v2/test_devfarm_commander.py tests/v2/test_devfarm_capability_probe.py tests/v2/test_gate_checker.py -q
```

- [ ] **Step 2: Run architecture and compile checks.**

```powershell
python scripts/check_architecture.py
python -m compileall -q src recovery scripts
```

Expected: `ARCHITECTURE_PASS` and exit code 0.

- [ ] **Step 3: Run the complete v2 regression with a captured log.**

```powershell
$log = Join-Path $env:TEMP 'dev-agent-v2-final-pytest.log'
python -m pytest tests/v2 -q *> $log
$code = $LASTEXITCODE
Get-Content -LiteralPath $log -Tail 12
exit $code
```

Record the actual count and exit code; do not reuse an older count.

- [ ] **Step 4: Synchronize documentation from observed evidence.**

Record the exact code commit, focused/full results, architecture result, compile result, real Worker evidence, G6O1 `NOT VERIFIED / FROZEN / NON_BLOCKING`, and explicit non-targets.  Do not mark a probe, dogfood, G6O1, or GitHub protection item successful without its direct evidence.

- [ ] **Step 5: Commit the documentation/evidence slice and verify the tree.**

```powershell
git diff --check
git status --short
git add docs/CURRENT_STATE.md docs/V2_EXECUTION_PLAN.md docs/CODEX_SUPERVISOR.md spec/v2/TRACEABILITY.md spec/v2/evidence/supervisor-dogfood-*.json
git commit -m "docs: record supervisor dogfood evidence"
git status --short --branch
```

- [ ] **Step 6: Push and inspect exact-head CI only after local verification.**

```powershell
git push origin v2/bootstrap
$runId = gh run list --branch v2/bootstrap --limit 1 --json databaseId --jq '.[0].databaseId'
gh run view --json headSha,status,conclusion,jobs $runId
```

Accept CI evidence only when `headSha` equals the pushed commit.  Leave GitHub required-check configuration and OS sandbox capability as external items if they remain unconfigured.
