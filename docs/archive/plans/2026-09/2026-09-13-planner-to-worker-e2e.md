# Model-catalog admission and Planner-to-Worker implementation plan

> Status: IMPLEMENTATION AND EXACT-HEAD CI VERIFIED on 2026-09-14. The live D1/D2 evidence is recorded in `spec/v2/evidence/planner-l2-live-d1-20260914.json` and `spec/v2/evidence/planner-to-worker-e2e-20260914.json`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make exact provider-model availability, externally sourced intelligence evidence, capability evidence, and existing runtime admission compose into Router candidates without weakening qualification, billing, privacy, quota, or reconciliation authority; then complete D1/D2 with a real Free L2 Planner and Free L1 Worker.

**Architecture:** Keep discovery, benchmark, capability, and runtime data as separate immutable inputs. A Host-only model-admission resolver supplies exact model evidence to the existing `ResourceRouter` and `OperationProviderBinding` composition; it does not create a second Router or fallback loop. `QualificationResolver` remains the exact, current integration-compatibility gate, while benchmark snapshots determine a known model's intelligence tier and task-fit policy.

**Tech Stack:** Python stdlib, existing ProviderFactory/Registry/Dispatcher, ResourceLedger/ResourceRouter, JSON snapshots, existing CLI scripts, pytest.

**Spec:** User instruction of 2026-09-14; `docs/requirements/multi-free-provider/02-architecture-intelligence-and-task.md`; `docs/requirements/multi-free-provider/03-resource-quota-capability-and-routing.md`; `docs/V2_DETAILED_ROADMAP.md` D1/D2.

## Global Constraints

- Preserve existing `ProviderDispatcher` failover-safe and UNKNOWN/reconciliation semantics; never add a Planner-only fallback loop.
- API discovery is read-only and explicit. Discovery never silently edits trusted routing snapshots or activates a credential merely because it exists.
- No model becomes routable from discovery or benchmark data alone: exact qualification, trusted billing, privacy, quota, health, and explicit binding configuration remain required.
- Do not infer L1/L2/L3 from a model name. Do not automatically downgrade an L2 Planner request to L1.
- Performance probing remains contract-only and bounded; it is not a tier-promotion mechanism.
- Preserve `STATIC_ONLY`, Host Verification, protected paths, Budget, Gate, and Human authority.

---

### Task 1: Immutable model evidence and alias catalogs

**Files:**
- Create: `src/dev_agent/resources/model_catalog.py`
- Create: `src/dev_agent/resources/model_benchmarks.py`
- Create: `src/dev_agent/resources/model_capabilities.py`
- Create: `tests/v2/test_model_catalog_admission.py`
- Create: `spec/v2/model_evidence/model_catalog_snapshot.json`
- Create: `spec/v2/model_evidence/model_alias_catalog.json`
- Create: `spec/v2/model_evidence/model_benchmark_snapshot.json`
- Create: `spec/v2/model_evidence/model_capability_snapshot.json`

**Interfaces:**
- Produces exact canonical model identity lookup, snapshot validation, intelligence score/tier policy, and task-fit/capability lookup.
- Consumes provider/binding/model identity only; does not read credentials, run providers, or mutate resources.

- [x] Write red tests for exact alias matching, expired/malformed snapshot rejection, unknown benchmark identity rejection, task-fit lookup, and score-configured tier calculation.
- [x] Run the focused test and observe missing-module failures.
- [x] Add immutable JSON-safe catalogs with exact identity keys, source/observed-at/model-version provenance, no fuzzy aliases, and data-defined tier thresholds.
- [x] Add minimal non-secret snapshots based only on observed official model metadata and benchmark references; unknown/missing evidence remains unadmitted.
- [x] Re-run the focused test and commit the independently testable data-model slice (`d0c6852`).

### Task 2: Explicit provider discovery adapters

**Files:**
- Create: `src/dev_agent/providers/model_discovery.py`
- Create: `scripts/refresh_model_catalog.py`
- Modify: `tests/v2/test_model_catalog_admission.py`
- Modify: `src/dev_agent/security/protected_paths.py`

**Interfaces:**
- Produces an untrusted, reviewable discovery snapshot from an explicitly named binding.
- Supports documented Gemini, Cloudflare Workers AI, OpenRouter, Groq, Mistral, SambaNova, Ollama local/cloud, and Vercel model-list response shapes.
- Never writes a trusted catalog path unless a protected, explicit operator action approves it.

- [x] Write red parser tests for Gemini `models`, OpenAI-compatible `data`, Cloudflare `result`, and Ollama `models` response shapes, plus unknown-provider and malformed-document rejection.
- [x] Run the focused tests and observe the missing discovery boundary.
- [x] Implement bounded stdlib HTTP discovery with secret values resolved only from the named environment variable and no raw response/credential logging.
- [x] Implement the refresh CLI as a candidate-artifact writer with bounded output and digest; existing output replacement requires explicit `--replace-existing`.
- [x] Re-run focused discovery tests and commit the discovery slice (`d0c6852`).

### Task 3: Host-only model admission in the existing Router

**Files:**
- Create: `src/dev_agent/resources/model_admission.py`
- Modify: `src/dev_agent/resources/router.py`
- Modify: `src/dev_agent/operation.py`
- Modify: `scripts/devfarm_planner_shadow.py`
- Modify: `tests/v2/test_model_catalog_admission.py`
- Modify: `tests/v2/test_planner_provider_pool.py`

**Interfaces:**
- `ModelAdmissionResolver.resolve(provider_id, binding_id, model_id)` returns only exact discovered, benchmarked, capability-known model evidence.
- `ResourceRouter` intersects existing persisted/qualification capabilities with model capability evidence; benchmark tier replaces legacy qualification tier when evidence is configured.
- `RouteRequest` can express an optional task-fit preference or minimum without changing unrelated callers.

- [x] Write red tests proving discovery-only, benchmark-only, capability-missing, and expired evidence cannot route; a high-score L2 with valid existing admission can route; and task-fit affects only the soft score after hard filters.
- [x] Run the focused Router/Planner tests and observe the expected failures.
- [x] Inject the resolver at Operation and Planner composition roots; retain legacy tier fields only for compatibility when no model-evidence resolver is configured.
- [x] Confirm unknown external outcome still never invokes an alternate binding and a confirmed unavailable L2 can use a distinct, admitted L2 binding.
- [x] Re-run focused Router/Planner tests and commit the admission integration slice (`d0c6852`).

### Task 4: Evidence-backed L2 inventory and bounded D1/D2 continuation

**Files:**
- Modify: `spec/v2/PROVIDER_CAPABILITY_MATRIX.json` only after minimal contract evidence for an exact binding/model exists.
- Modify: `src/dev_agent/resources/billing_catalog.py` only after exact no-charge/billing authority is known.
- Create: `spec/v2/evidence/model-catalog-refresh-20260914.json`
- Create: `spec/v2/evidence/planner-to-worker-e2e-20260914.json` on success.
- Modify: `docs/V2_DETAILED_ROADMAP.md`, `docs/CURRENT_STATE.md`, `docs/PROVIDER_FAILURE_PLAYBOOK.md`, `spec/v2/TRACEABILITY.md` only where evidence changes.

**Interfaces:**
- Consumes reviewed discovery snapshots, benchmark snapshots, capability contract evidence, current qualification, and billing profiles.
- Produces a bounded, exact L2 pool for D1, then existing Bridge/Commander/Supervisor artifacts for D2.

- [x] Run a read-only discovery refresh for configured providers and record only sanitized counts/identities/digests (`1289` entries from `12/14` bindings; two typed failures).
- [x] For each candidate L2 binding, run one bounded integration contract qualification; do not run model-performance batteries and do not elevate from a benchmark alone (`gemini:worker:free-3/-4/-5`, text-only unknown-quota scope).
- [x] Run one bounded pool-routed D1 observation. It exhausted two confirmed-unavailable exact L2 bindings without a proposal; preserve the sanitized result in evidence.
- [x] Continue immediately through existing Bridge → Commander → L1 Worker → Host Verification → ReviewDecision → deterministic integration, with target-child Codex implementation count zero. Verified by `planner-to-worker-e2e-20260914.json`.
- [x] Run focused tests (`24 passed` after the compatibility fix), full `tests/v2` (`993 passed, 1 skipped`), architecture check, and compileall.
- [x] Push the synchronized evidence/documentation commit and confirm exact-head CI. `v2-core` run `34790435399` and `v2-provider-smoke` run `34790435394` passed for branch commit `8c25513`.

## Current facts after the model-evidence slice

- `4e96630` is the implementation baseline for the D1/D2 evidence plus the Python 3.11 compatibility fixes; synchronized branch commit `8c25513` passed exact-head `v2-core` and `v2-provider-smoke`. The two earlier collection failures are retained as diagnostic history.
- The reviewed evidence stack represents four exact Gemini L2 identities: `gemini:core` and `gemini:worker:free-3/-4/-5`. The latter three have current high-confidence text-only integration qualification; the separate benchmark snapshot supplies the L2 tier.
- Read-only model-list observations produced `1289` entries from `12/14` explicit bindings. `groq` and local `ollama` discovery failures are recorded as typed bounded failures; no availability is inferred.
- The first bounded D1 pool observation used `free-4` and `free-5`; both returned confirmed provider-unavailable and the pool ended `pool_exhausted`. A later bounded pool run selected `free-3` / `gemini-3.6-flash` and passed D1; D2 then passed through Host integration. Prior failures remain evidence, not retry instructions.

## Non-goals

No automatic benchmark scraping, no automatic trusted-snapshot replacement, no paid-provider enablement, no Compression Service, no OpenAI/Claude API, no MCP runtime, no new scheduler/state machine/fallback manager, no qualification bypass, and no Codex implementation of the D2 target child.
