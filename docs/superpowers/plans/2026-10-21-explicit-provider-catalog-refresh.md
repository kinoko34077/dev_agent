# Explicit Provider Catalog Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator refresh candidate model-catalog evidence for only explicitly selected Providers, so a local Ollama refresh cannot contact unrelated configured remote Providers.

**Architecture:** Extend the existing `scripts/refresh_model_catalog.py` binding selection path; do not add another discovery client or evidence store. Provider filtering occurs after loading the existing binding configuration and before constructing/invoking the discovery client. Existing explicit-output, candidate-only, and no-admission semantics remain unchanged.

**Tech Stack:** Python 3.10+, argparse, pytest, existing `ProviderModelDiscovery`.

**Spec:** `docs/requirements/multi-free-provider/03-resource-quota-capability-and-routing.md`; operator boundary in `docs/CODEX_DAILY_DOGFOOD.md`.

## Global Constraints

- Catalog refresh remains read-only discovery and writes only to the explicit `--output` candidate path.
- Discovery does not establish aliases, capabilities, benchmark, qualification, billing, quota, health, runtime admission, or Phase 8 supply.
- Provider selection must be applied before any Provider request; `ollama` must not select `ollama_cloud`.
- An unknown or unconfigured requested Provider fails before any network call.
- With no selector, preserve existing CLI behavior for compatibility.
- Do not read, print, persist, or transmit credential values; do not change credentials, canonical snapshots, billing, admission, Gate, or runtime state.

## Review Focus

- **Selection-before-I/O:** a requested Provider missing from config must fail before discovery; test that the discovery stub receives zero calls.
- **Exact Provider identity:** selecting `ollama` must not include `ollama_cloud`; test exact matching.
- **Multiple filters:** repeatable `--provider` values select the union once each; test deterministic output and no duplicate discovery.
- **Compatibility:** no `--provider` retains current all-configured behavior; test this explicitly.
- **Candidate-only boundary:** selected refresh writes only the requested candidate output and does not modify a canonical catalog; test the output identity and unchanged base bytes.

---

### Task 1: Add explicit Provider filtering to catalog refresh

**Files:**
- Modify: `scripts/refresh_model_catalog.py`
- Test: `tests/v2/test_model_catalog_admission.py`

**Interfaces:**
- Consumes: existing `load_bindings(path) -> tuple[ModelDiscoveryBinding, ...]`, `refresh(bindings, ...)`, `write_candidate(...)`.
- Produces: `select_bindings(bindings: tuple[ModelDiscoveryBinding, ...], provider_ids: tuple[str, ...] | None = None) -> tuple[ModelDiscoveryBinding, ...]`; repeatable CLI flag `--provider PROVIDER_ID`.
- `select_bindings` returns the original ordered tuple when `provider_ids` is absent/empty; otherwise it selects configured bindings whose exact `provider_id` is requested, preserving configuration order. If any requested Provider has no configured binding, it raises `ValueError` before discovery.

- [x] **Step 1: Write the failing selection and CLI tests**
  - Cover no filter, exact `ollama` versus `ollama_cloud`, multiple Provider IDs, unknown Provider with zero discovery calls, and candidate output without canonical-file mutation.
  - Invoke `main([...])` with a temporary binding file and a discovery stub so the test proves the CLI applies the filter before network I/O.

- [x] **Step 2: Run the focused tests and observe the expected failure**
  - Run: `python -m pytest tests/v2/test_model_catalog_admission.py -q`
  - Expected: FAIL because `select_bindings` and `--provider` do not exist yet.

- [x] **Step 3: Implement exact Provider selection**
  - Add `select_bindings` and repeatable `--provider` parsing in `scripts/refresh_model_catalog.py`.
  - Apply selection after `load_bindings` and before `refresh`; keep `refresh` responsible only for the selected bindings.
  - If a selected Provider has no configured binding, return the script's existing sanitized failure shape without invoking discovery.

- [x] **Step 4: Run focused tests and inspect the diff**
  - Run: `python -m pytest tests/v2/test_model_catalog_admission.py -q`
  - Expected: PASS, including selection-before-I/O and canonical-file immutability cases.
  - Run: `git diff --check`
  - Expected: no whitespace errors.

- [x] **Step 5: Commit the implementation slice**
  - Commit only the script, focused tests, and this plan after checking the exact diff; do not commit generated local discovery output.
