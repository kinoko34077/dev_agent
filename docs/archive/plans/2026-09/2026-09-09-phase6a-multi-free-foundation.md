# Phase 6A Multi-Free Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add a durable quota-domain and resource-observation foundation so the existing ProviderDispatcher can select among multiple free/local resources without changing the current paid-provider gate.

**Architecture:** Extend the existing ResourceLedger with an ordered schema migration for resource identity, quota observations, and current operational observations. Keep the ProviderDispatcher and Controller boundaries unchanged; ResourceRouter consumes only normalized ledger values and fails closed on stale quota data when a resource declares a quota domain. New cloud adapters are separate, injected-transport providers and receive no SDK objects.

**Tech Stack:** Python 3.10/3.11, standard-library sqlite3/urllib, pytest, Markdown, GitHub Actions.

**Spec:** docs/requirements/multi-free-provider/00-index.md and chapters 03, 04, and 07.

## Global Constraints

- Keep G6O1 BLOCKED_EXTERNAL; do not make paid requests or invent paid evidence.
- Preserve the canonical Controller -> ProviderDispatcher -> ProviderRegistry -> Concrete Provider path.
- Do not implement intelligence tiers, hedging, AgentBackend, MCP/API, Phase 7, or large Controller rewrites.
- Keep existing ResourceLedger schema data migratable and preserve all current tests.
- Every production behavior change starts with a failing test and ends with local tests, commit, push, and exact-head CI.

### Task 1: Add quota-domain identity and ordered ledger migration

**Files:**
- Modify: src/dev_agent/resources/ledger.py
- Test: tests/v2/test_phase6_resources.py

**Interfaces:**
- Extend ResourceSpec with optional quota_domain: str | None.
- Extend register_resource with quota_domain: str | None.
- Add ResourceLedger.SCHEMA_VERSION 6.
- Add resources.quota_domain and quota_observations table through ordered migration v4 -> v5, then operational observation columns through v6.
- Return quota_domain through get_resource and list_resources.

- [x] Step 1: Add failing tests for quota_domain persistence, schema version 6, and legacy migration.
- [x] Step 2: Run those tests and confirm failure because quota_domain and migration v5 do not exist.
- [x] Step 3: Add the v5 migration, strict quota-domain validation, and persisted resource identity.
- [x] Step 4: Run the focused tests and confirm they pass.
- [x] Step 5: Run the existing resource migration tests and commit the ledger identity batch (`10b322d`).

### Task 2: Add durable quota observations

**Files:**
- Modify: src/dev_agent/resources/ledger.py
- Test: tests/v2/test_phase6_resources.py

**Interfaces:**
- Add QuotaObservation frozen dataclass with domain, request/token limits and remaining values, reset_at, daily_remaining, concurrency_limit, confidence, observed_at, and source.
- Add ResourceLedger.observe_quota(resource_id, request_limit=None, request_remaining=None, token_limit=None, token_remaining=None, reset_at=None, daily_remaining=None, concurrency_limit=None, confidence=1.0, observed_at=None, source="provider") -> None.
- Add ResourceLedger.get_quota_observation(resource_id) -> dict[str, Any] | None.
- Resource quota observations must be integer/non-negative where applicable, bounded by their limit, timestamped, and associated with the resource quota_domain.

- [x] Step 1: Add failing tests for durable observation, input validation, and restart reload.
- [x] Step 2: Run the focused tests and confirm failure because the API is absent.
- [x] Step 3: Implement the observation dataclass, validation, insert/update of the latest resource snapshot, and read API.
- [x] Step 4: Run focused tests and confirm they pass.
- [x] Step 5: Commit the quota observation batch (`dd1a486`).

### Task 3: Expand operational resource observations and quota-aware routing

**Files:**
- Modify: src/dev_agent/resources/ledger.py
- Modify: src/dev_agent/resources/router.py
- Test: tests/v2/test_phase6_router.py
- Test: tests/v2/test_phase6_resources.py

**Interfaces:**
- Extend ResourceLedger.observe with optional quota_remaining_ratio, quota_reset_at, latency_ewma_ms, failure_ewma, inflight, and concurrency_limit values.
- Extend RouteRequest with max_quota_observation_age_seconds: float | None, defaulting to 300 seconds.
- Add quota-aware hard filtering for declared quota domains: missing, future, or stale quota observation is ineligible when the request requires fresh quota.
- Add deterministic preference for higher fresh quota headroom before effective cost, while retaining privacy and capability hard filters.
- Extend RouteSelection only when needed for existing callers; do not duplicate provider selection in Controller.

- [x] Step 1: Add failing tests for operational observation persistence, stale/future quota rejection, and higher-quota selection.
- [x] Step 2: Run focused tests and confirm failure.
- [x] Step 3: Implement validation, persistence, freshness checks, and deterministic routing.
- [x] Step 4: Run router/resource tests and confirm they pass.
- [x] Step 5: Commit the quota-aware routing batch (`dd1a486`).

### Task 4: Add separate free-provider adapter boundaries

**Files:**
- Create: src/dev_agent/providers/groq/__init__.py
- Create: src/dev_agent/providers/groq/provider.py
- Create: src/dev_agent/providers/cloudflare/__init__.py
- Create: src/dev_agent/providers/cloudflare/provider.py
- Create: src/dev_agent/providers/mistral/__init__.py
- Create: src/dev_agent/providers/mistral/provider.py
- Create: src/dev_agent/providers/openrouter/__init__.py
- Create: src/dev_agent/providers/openrouter/provider.py
- Test: tests/v2/test_provider_contracts.py
- Modify: src/dev_agent/providers/__init__.py only if exports are required

**Interfaces:**
- GroqProvider, CloudflareWorkersAIProvider, MistralProvider, and OpenRouterFreeProvider implement ModelProvider through injected transport callables.
- All adapters normalize ModelResponse and re-raise typed ProviderError before broad transport wrapping.
- Provider-specific payload construction is private to each adapter; no SDK objects cross the ModelProvider boundary.
- No live cloud request is made by tests or CI. Live qualification remains separate evidence and G6O1 remains blocked.

- [x] Step 1: Add failing contract tests for the free-provider adapters and typed error preservation.
- [x] Step 2: Run focused tests and confirm failure because the modules do not exist.
- [x] Step 3: Implement the smallest injected-transport adapters using existing normalize_response behavior.
- [x] Step 4: Run provider contract tests and the complete v2 suite.
- [x] Step 5: Commit and push the adapters only after all local checks pass (`a414907`, cleanup `0a32d38`).

### Task 5: Update Phase 6A evidence and verify

**Files:**
- Modify: docs/PHASE6_PLAN.md
- Modify: docs/requirements/multi-free-provider/00-index.md
- Modify: CHANGELOG.md
- Do not change: spec/v2/GATE_STATUS.json statuses without new evidence

**Interfaces:**
- Documentation identifies quota_domain/quota observation/resource observation as implemented only when tests and CI verify them.
- Provider live evidence is not claimed from injected-transport tests.
- G6O1 remains BLOCKED_EXTERNAL and Phase 7 remains deferred.

- [x] Step 1: Run full pytest, compileall, JSON validation, and relevant gate checks; `check_head` follows the clean commit.
- [x] Step 2: Update Current State with exact local test count and external CI run IDs (`267 passed, 1 skipped`; v2-core `34318901211`; v2 tests `34318901190`; code baseline `0d690d8`).
- [x] Step 3: Inspect diff and verify no forbidden future feature was added.
- [x] Step 4: Commit, push, and confirm exact-head CI for the final documentation commit (`5ce4a92`; v2-core `34319216207`; v2 tests `34319216218`).
- [x] Step 5: Stop at a clean tested boundary and record remaining provider live qualification work.

## Execution record

- `7957dbd`: plan and scope record.
- `10b322d`: quota-domain identity and ResourceLedger schema v5 migration.
- `dd1a486`: durable quota observations, schema v6 operational observations, and quota-aware routing.
- `a414907`, `0a32d38`: Groq/Cloudflare contract adapters and adapter file cleanup.
- `0d690d8`: Mistral/OpenRouter Free contract adapters, Provider exports, Groq-backed quota/outage dispatch tests, expired-lease fencing, and Current State synchronization.
- External exact-head CI for `0d690d8`: v2-core `34318901211` and v2 tests `34318901190` both success.

Remaining after this batch: live qualification for the new cloud providers,
provider-owned quota observation ingestion, multi-credential quota aggregation,
and later task-specific scoring, hedging, and Phase 7 intelligence/backend
features. G6O1 remains BLOCKED_EXTERNAL.
