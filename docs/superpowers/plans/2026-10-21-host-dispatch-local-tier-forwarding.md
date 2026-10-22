# Host Dispatch Local-Tier Forwarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Preserve the Host-selected local-trial tier across the one-shot Host dispatch boundary, then attempt the local operational cycle only if current no-charge admission independently passes.

**Architecture:** Keep route selection and admission in existing Core/DevFarm code. When the selected concrete provider is Ollama, pass the already-selected tier to the bounded child environment; the child still reconstructs its configured binding and independently re-runs current no-charge admission. No remote-provider tier or authority is inferred.

**Tech Stack:** Python, existing HostProcessExecutor, pytest, DevFarm Supervisor.

**Spec:** `docs/superpowers/specs/2026-10-21-risk-bounded-autonomous-development-design.md`; this is a narrow defect follow-up, not a Phase 8 Gate change.

## Global Constraints

- Use only the existing local `qwen3.5:9b` lane for this bounded trial; do not enable paid providers or alter qualification/Gate authority.
- Keep provider identity, endpoint, credentials, and raw model output out of logs and evidence.
- Never replay the failed attempt; any new live attempt must have fresh task/request/attempt identities.
- Preserve `spec/v2/GATE_STATUS.json` and report the operational trial separately from Formal Phase 8.

## Review Focus

- Local Ollama L1 selected by the parent must reach the child without secrets, while child-side billing/privacy/admission checks remain active.
- Non-Ollama dispatch must not receive an Ollama-specific tier override.
- Missing selected tier must not be silently invented; existing envelope validation remains authoritative.
- Host runtime rejection must remain pre-effect and non-retryable unless independently proven safe.

---

### Task 1: Preserve the selected Ollama tier at the Host process boundary

**Files:**
- Modify: `src/dev_agent/providers/host_dispatch.py`
- Test: `tests/v2/test_host_provider_dispatch.py`

**Interfaces:**
- Consumes: `HostProviderDispatch.envelope()` and the selected provider identity.
- Produces: Ollama child environment includes the envelope's selected `intelligence_tier`; other providers receive no Ollama tier override.

- [x] **Step 1: Add a failing boundary test**

Add a test whose fake one-shot child returns `OLLAMA_INTELLIGENCE_TIER` in a bounded response. With an Ollama provider selected at `L1`, assert the child observes exactly `L1`.

- [x] **Step 2: Run the test and confirm the expected failure**

Run: `pytest tests/v2/test_host_provider_dispatch.py -k selected_tier -q`
Expected: the child currently observes no tier and the assertion fails.

- [x] **Step 3: Forward only the already-selected Ollama tier**

Build the bounded envelope before spawning the child and set `OLLAMA_INTELLIGENCE_TIER` only when `provider_id == "ollama"`; do not alter route selection, retry, or child-side admission.

- [x] **Step 4: Verify the focused boundary tests**

Run: `pytest tests/v2/test_host_provider_dispatch.py -k "selected_tier or ollama_timeout" -q`
Expected: PASS.

- [x] **Step 5: Recheck the exact configured L1 local admission without generating**

Run a read-only diagnostic using the same configured pool, evidence catalog, and no-charge L1 admission as the child. Expected: only a current, reviewed no-charge profile can admit `ollama:local:qwen3.5-9b`; print no endpoint or credential values. If the existing profile is expired, stop before generation and record the budget-authority blocker; do not edit the billing catalog as part of this task.

- [ ] **Step 6: Run one fresh bounded operational cycle only if admission passes**

**Current result: BLOCKED before generation.** The configured qwen3.5:9b no-charge profile is no longer current (review window ended 2026-10-09), so exact L1 local admission returns zero candidates. Do not refresh budget authority or generate until the existing authorized profile path supplies current evidence.

Only after current no-charge admission passes, create fresh root/task/request/attempt identities and use the existing Supervisor with `qwen3.5:9b`. Do not resume or replay the rejected attempt. Stop at accepted patch, a new typed blocker, or the configured bounded attempt limit.

- [ ] **Step 7: Record result in owning Issue #97**

**Current result: pending valid Issue write path.** Devflow read tools failed; the connected GitHub comment writer is PR-only and rejected an Issue target. Keep the checkpoint in the local ledger; do not route around the rejected target.

Record the diagnosis, minimal fix, focused evidence, whether a fresh patch reached Host Verification, and the next safe action. Do not change Formal Gate status.
