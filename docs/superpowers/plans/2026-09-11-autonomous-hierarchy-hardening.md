# Autonomous Hierarchy Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the existing Resource, Provider, Intelligence, Operation, DevFarm, Commander, and AgentBackend boundaries into a bounded, restart-safe hierarchy without adding a parallel scheduler, state machine, or agent framework.

**Architecture:** Keep `SQLiteStateStore`, `DurableQueue`, `ResourceControlPlane`, `ProviderDispatcher`, `Controller`, `FiniteLifecycleLoop`, `TaskLifecycleCoordinator`, `DevFarm`, and `AgentBackendDispatcher` as the authorities they already own. Add only projections, admission checks, per-binding liveness, and thin composition at existing boundaries; preserve UNKNOWN/reconciliation and explicit review semantics.

**Tech Stack:** Python 3.10/3.11, existing SQLite stores and migrations, pytest, Git worktrees, existing ProviderFactory/Registry/Dispatcher, existing DevFarm Host Verification, no new runtime framework or dependency.

**Spec:** `docs/requirements/autonomous-hierarchy-hardening/00-index.md` and chapters `01`–`06`.

## Global Constraints

- Work only on `v2/bootstrap`; `main` remains the frozen v1 line.
- Preserve the existing public Provider, StateStore, Queue, Resource, Recovery, DevFarm, Commander, and AgentBackend contracts unless a backward-compatible extension is required by a failing test.
- Do not modify `spec/v2/GATE_STATUS.json` to claim progress from local tests or model self-reporting.
- Do not add LangGraph, CrewAI, Temporal, MCP, Codex App Server, A2A, AG-UI, a second scheduler, a second StateStore, or a second Budget system in this plan.
- Treat `text`, `tool_call`, `structured_output`, `json`, and `long_context` as routing capabilities; keep qualification and invariant evidence separate.
- Treat billing, capability qualification, privacy, and operator activation as separate authorities and combine them only in an effective eligibility decision.
- An external outcome, quota, billing fact, or qualification that is unknown is never treated as success, free, or available without the explicitly bounded UNKNOWN policy.
- Every production-code change follows RED → focused failing test → minimal GREEN implementation → focused regression → full `tests/v2` regression.
- Every verified slice is committed and pushed to `origin/v2/bootstrap`; external blockers remain explicitly documented.

---

### Task 1: Re-establish baseline and create audit regression shells

**Files:**
- Create: `tests/v2/test_capability_qualification_projection.py`
- Create: `tests/v2/test_provider_saturation_and_wake.py`
- Create: `tests/v2/test_agent_backend_race.py`
- Create: `tests/v2/test_operation_hierarchy_e2e.py`
- Create: `tests/v2/test_planner_privacy_boundaries.py`
- Modify: `docs/CURRENT_STATE.md` only after a verified implementation slice

**Interfaces:**
- Consumes: current `OperationService`, `ResourceRouter`, `TaskIntelligencePolicy`, `AgentBackendDispatcher`, `FiniteLifecycleLoop`, `TaskGraph`, and existing test helpers.
- Produces: named failing/regression tests that map to A1–A4, B1–B4, C1–C3, D1–D3, E1, and G1–G2 without changing Gate status.
- Test helpers: `open_operation_with_qualified_tool_binding(tmp_path) -> OperationService` and `open_test_service(tmp_path) -> OperationService` are defined in the new test modules and use only existing fake providers/resources.

- [ ] **Step 1: Record the current checkout and test baseline**

  Run:

  ```powershell
  git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" branch --show-current
  git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" rev-parse HEAD
  git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" status --short --branch
  python -m pytest -q tests/v2 --durations=10
  ```

  Expected: branch `v2/bootstrap`, clean worktree before test creation, and the current recorded baseline or a newly measured result. Preserve the exact output in the task notes, not in Gate status.

- [ ] **Step 2: Write one failing test for the first unresolved capability behavior**

  Use a qualified resource whose raw matrix evidence contains `model_generated_tool_call` and `tool_result_roundtrip`, then assert that a `tool_call` request routes. The test must fail because the current Operation resource projection exposes only `text`.

  ```python
  def test_qualified_tool_evidence_projects_to_tool_call_for_operation_route(tmp_path):
      service = open_operation_with_qualified_tool_binding(tmp_path)
      task = service.submit(service.config, "run a tool", required_capabilities=["tool_call"])
      assert service.controller.dispatch_task(task).status is TaskStatus.COMPLETED
  ```

- [ ] **Step 3: Run only the new test and confirm the expected failure**

  Run `python -m pytest -q tests/v2/test_capability_qualification_projection.py::test_qualified_tool_evidence_projects_to_tool_call_for_operation_route`.

  Expected: a routing failure caused by the missing `tool_call` projection, not a fixture import error.

- [ ] **Step 4: Add test names for the remaining audit boundaries**

  Add tests with these exact behaviors: provider lane isolation, saturation wake, strict AgentBackend start race, sensitive child rejection, and Operation lifecycle composition. Keep each test independent and use existing fake providers/backends.

- [ ] **Step 5: Commit the test-only baseline**

  ```powershell
  git add tests/v2/test_capability_qualification_projection.py tests/v2/test_provider_saturation_and_wake.py tests/v2/test_agent_backend_race.py tests/v2/test_operation_hierarchy_e2e.py tests/v2/test_planner_privacy_boundaries.py
  git commit -m "test: map autonomous hierarchy hardening gaps"
  git push origin v2/bootstrap
  ```

### Task 2: Add canonical capability qualification projection

**Files:**
- Create: `src/dev_agent/resources/qualification.py`
- Modify: `src/dev_agent/resources/router.py`
- Modify: `src/dev_agent/operation.py`
- Modify: `src/dev_agent/resources/catalog.py` only where the existing Resource read view needs the derived projection
- Test: `tests/v2/test_capability_qualification_projection.py`
- Test: `tests/v2/test_operation.py`
- Test: `tests/v2/test_intelligence_routing.py`

**Interfaces:**
- Consumes: `spec/v2/PROVIDER_CAPABILITY_MATRIX.json`, existing catalog/binding identity, existing `ResourceRouter` hard filters.
- Produces: `QualificationProjection` and `QualificationResolver.resolve(provider_id, provider_binding_id, model_id, now=None)`; the resolver returns current canonical routing capabilities, tier, expiry, and evidence metadata without mutating Resource catalog rows.

- [ ] **Step 1: Define the projection types and vocabulary test**

  Add a frozen projection with `provider_id`, `provider_binding_id`, `model_id`, `routing_capabilities`, `intelligence_tier`, `tested_at`, `expires_at`, `confidence`, `qualification_evidence`, and `integration_evidence`. Reject unknown routing vocabulary and keep raw evidence strings out of `routing_capabilities`.

  ```python
  CANONICAL_ROUTING_CAPABILITIES = frozenset({"text", "tool_call", "structured_output", "json", "long_context"})
  ```

- [ ] **Step 2: Run projection tests RED**

  Run `python -m pytest -q tests/v2/test_capability_qualification_projection.py -k projection` and confirm the new resolver is absent or the expected projection assertions fail.

- [ ] **Step 3: Implement resolver rules**

  Resolve an exact provider/binding/model entry, reject expired or missing qualification, derive `tool_call` only from the required observed evidence, and map only canonical execution capabilities to the Resource routing view. Do not infer a tier from a model name when the exact qualification is absent or expired.

- [ ] **Step 4: Wire Operation resource creation to the resolver**

  Replace the new-resource `capabilities=["text"]` projection with the resolver result when an exact current qualification exists. Preserve `ResourceLedger` operator identity, pricing, quota domain, health, and metadata on existing resources; do not upsert them during `OperationService.open()`.

- [ ] **Step 5: Make Router consume effective capabilities**

  Pass only the derived canonical capabilities to `RouteRequest`. A Task competency such as `architecture` must remain a policy/tier input and must not become a Resource capability requirement.

- [ ] **Step 6: Run focused and legacy capability tests**

  Run `python -m pytest -q tests/v2/test_capability_qualification_projection.py tests/v2/test_operation.py tests/v2/test_intelligence_routing.py`.

- [ ] **Step 7: Commit the projection slice**

  ```powershell
  git add src/dev_agent/resources/qualification.py src/dev_agent/resources/router.py src/dev_agent/operation.py src/dev_agent/resources/catalog.py tests/v2/test_capability_qualification_projection.py tests/v2/test_operation.py tests/v2/test_intelligence_routing.py
  git commit -m "feat: project qualified provider capabilities"
  git push origin v2/bootstrap
  ```

### Task 3: Separate Task traits and enforce privacy inheritance

**Files:**
- Modify: `src/dev_agent/domain/protocol.py`
- Modify: `src/dev_agent/intelligence/policy.py`
- Modify: `src/dev_agent/operation.py`
- Modify: `src/dev_agent/intelligence/target.py`
- Test: `tests/v2/test_intelligence.py`
- Test: `tests/v2/test_operation.py`
- Test: `tests/v2/test_planner_privacy_boundaries.py`

**Interfaces:**
- Consumes: existing `Task.required_capabilities`, `Task.sensitivity`, `TaskIntelligencePolicy`, `OperationService.submit_child()`.
- Produces: a backward-compatible classification/resolution for execution capabilities vs competency/policy traits, and monotonic child sensitivity validation.

- [ ] **Step 1: Add failing tests for trait classification and child downgrade**

  ```python
  def test_architecture_trait_raises_tier_without_becoming_resource_capability(tmp_path):
      service = open_test_service(tmp_path)
      task = service.submit(service.config, "review architecture", required_capabilities=["architecture"])
      assert task.required_capabilities == ["architecture"]
      assert service.controller.intelligence_policy.decide(task).minimum_tier.value == "L2"
      assert all("architecture" not in resource.get("capabilities", []) for resource in service.ledger.list_resources())

  def test_sensitive_parent_rejects_public_child(tmp_path):
      service = open_test_service(tmp_path)
      parent = service.submit(service.config, "handle sensitive data", sensitivity="sensitive")
      with pytest.raises(ValueError, match="sensitivity"):
          service.submit_child(service.config, parent.task_id, "publish data", sensitivity="public")

  def test_normal_parent_allows_internal_child(tmp_path):
      service = open_test_service(tmp_path)
      parent = service.submit(service.config, "handle normal data", sensitivity="normal")
      child = service.submit_child(service.config, parent.task_id, "inspect data", sensitivity="internal")
      assert child.sensitivity == "internal"
  ```

  Assert the actual Task/route request values rather than private implementation calls.

- [ ] **Step 2: Run the focused tests RED**

  Run `python -m pytest -q tests/v2/test_planner_privacy_boundaries.py tests/v2/test_intelligence.py -k "trait or sensitivity"` and confirm the downgrade or capability assertion fails for the pre-fix path.

- [ ] **Step 3: Implement versioned capability classification**

  Keep old serialized `required_capabilities` readable. Classify known execution capabilities for Router use and treat `architecture`, `security`, `protected`, `recovery`, and similar values as competency/policy traits. Reject an unknown token at the public validation boundary with a typed `ProtocolError` or existing validation error.

- [ ] **Step 4: Enforce monotonic child sensitivity**

  In `submit_child()` and the planner-facing validation boundary, reject a child sensitivity lower than its parent. Permit equal or more restrictive values. Do not add a declassification shortcut.

- [ ] **Step 5: Run focused regression and commit**

  ```powershell
  python -m pytest -q tests/v2/test_planner_privacy_boundaries.py tests/v2/test_intelligence.py tests/v2/test_operation.py
  git add src/dev_agent/domain/protocol.py src/dev_agent/intelligence/policy.py src/dev_agent/operation.py src/dev_agent/intelligence/target.py tests/v2/test_planner_privacy_boundaries.py tests/v2/test_intelligence.py tests/v2/test_operation.py
  git commit -m "fix: separate task traits and privacy inheritance"
  git push origin v2/bootstrap
  ```

### Task 4: Make privacy and Ollama eligibility explicit

**Files:**
- Modify: `src/dev_agent/resources/catalog.py`
- Modify: `src/dev_agent/resources/qualification.py`
- Modify: `src/dev_agent/operation.py`
- Modify: `src/dev_agent/resources/router.py`
- Modify: `spec/v2/PROVIDER_CAPABILITY_MATRIX.json` only when new evidence exists
- Test: `tests/v2/test_operation.py`
- Test: `tests/v2/test_capability_qualification_projection.py`

**Interfaces:**
- Consumes: explicit Resource privacy policy, current qualification projection, existing Ollama adapter.
- Produces: effective privacy eligibility; Ollama is either measured at an exact tier or explicitly `survival-only/unclassified`, never model-name inferred.

- [ ] **Step 1: Add failing privacy route tests**

  Test that a normal remote Resource is rejected for a sensitive Task and an explicitly privacy-qualified local Resource is accepted. Test that an Ollama binding with no qualified tier is not an exact L1/L2 production candidate.

- [ ] **Step 2: Run RED**

  Run `python -m pytest -q tests/v2/test_capability_qualification_projection.py tests/v2/test_operation.py -k "privacy or Ollama or sensitive"`.

- [ ] **Step 3: Implement separate privacy profile**

  Add the smallest provider/resource metadata needed for `remote`, `local`, and maximum allowed sensitivity. Keep privacy authority separate from billing and qualification. Existing resources remain operator-owned and are not rewritten by startup.

- [ ] **Step 4: Define Ollama unclassified behavior**

  If no exact current qualification provides a tier, exclude Ollama from normal exact-tier routing while retaining its PRIVATE/SURVIVAL role. Only a real qualification artifact can activate a tier.

- [ ] **Step 5: Run GREEN and commit**

  ```powershell
  python -m pytest -q tests/v2/test_capability_qualification_projection.py tests/v2/test_operation.py tests/v2/test_intelligence_routing.py
  git add src/dev_agent/resources/catalog.py src/dev_agent/resources/qualification.py src/dev_agent/operation.py src/dev_agent/resources/router.py tests/v2/test_capability_qualification_projection.py tests/v2/test_operation.py tests/v2/test_intelligence_routing.py
  git commit -m "fix: enforce explicit privacy resource eligibility"
  git push origin v2/bootstrap
  ```

### Task 5: Expand protected authority and secret hygiene

**Files:**
- Modify: `src/dev_agent/security/protected_paths.py`
- Modify: `scripts/devfarm.py`
- Modify: `scripts/devfarm_commander.py`
- Modify: `src/dev_agent/backends/dispatcher.py`
- Modify: `.gitignore`
- Test: `tests/v2/test_security_boundaries.py`
- Test: `tests/v2/test_devfarm_manifest.py`

**Interfaces:**
- Consumes: shared `is_protected_path()` and current DevFarm/Commander ownership validation.
- Produces: one responsibility-based protected policy covering billing, qualification, activation, outbound secret validation, Host Verification, integration proof, AgentBackend dispatch, budget, recovery, credentials, `.env.*`, and `.devfarm`.

- [ ] **Step 1: Add failing protected-path and ignore tests**

  Assert that billing catalog, capability matrix, DevFarm trust-boundary scripts, qualification projection, and `.env.local` are rejected as Worker-owned paths; assert `.env.*` is ignored while `.env.example` remains shareable.

- [ ] **Step 2: Run RED**

  Run `python -m pytest -q tests/v2/test_security_boundaries.py tests/v2/test_devfarm_manifest.py -k "protected or env"`.

- [ ] **Step 3: Extend the shared policy**

  Add normalized responsibility paths to `PROTECTED_AUTHORITY_PATHS` or the existing directory/part rules. Keep the policy path-only and do not grant access. Ensure DevFarm, Commander, and AgentBackend use the same predicate.

- [ ] **Step 4: Update `.gitignore` safely**

  Add `.env.*` and an explicit exception for `.env.example`; inspect tracked files before declaring hygiene complete. Do not delete or rewrite existing user files.

- [ ] **Step 5: Run tests and commit**

  ```powershell
  python -m pytest -q tests/v2/test_security_boundaries.py tests/v2/test_devfarm_manifest.py tests/v2/test_devfarm_patch_validation.py
  git add src/dev_agent/security/protected_paths.py scripts/devfarm.py scripts/devfarm_commander.py src/dev_agent/backends/dispatcher.py .gitignore tests/v2/test_security_boundaries.py tests/v2/test_devfarm_manifest.py
  git commit -m "security: protect qualification and worker authority paths"
  git push origin v2/bootstrap
  ```

### Task 6: Isolate Provider execution lanes and wake saturation waits

**Files:**
- Modify: `src/dev_agent/runtime/model_turn.py`
- Modify: `src/dev_agent/runtime/controller.py`
- Modify: `src/dev_agent/providers/dispatcher.py`
- Modify: `src/dev_agent/scheduler/queue.py`
- Modify: `src/dev_agent/scheduler/worker.py`
- Modify: `src/dev_agent/operation.py`
- Test: `tests/v2/test_provider_saturation_and_wake.py`
- Test: `tests/v2/test_phase6_provider_reconciliation.py`

**Interfaces:**
- Consumes: current `ModelTurnExecutor`, `ProviderExecutionSaturated`, ProviderDispatcher binding selection, Queue `defer/wake`, and durable Task/Event state.
- Produces: binding-scoped execution saturation and a matching durable wake; `lease_claim_count` remains distinct from logical execution attempts.

- [ ] **Step 1: Add a hanging-provider test**

  Use a real blocking fake Provider. Assert that a Gemini-lane timeout does not make Cloudflare unavailable and that a Task parked for `resource:provider_execution_saturated:<binding>` can be woken when the lane capacity is released.

- [ ] **Step 2: Run the test RED**

  Run `python -m pytest -q tests/v2/test_provider_saturation_and_wake.py` and verify the failure is the missing lane isolation or wake, not a timing typo. Use an Event/Barrier rather than a long sleep.

- [ ] **Step 3: Introduce per-binding execution ownership**

  Keep unkillable Python thread limits. Scope orphan/saturation state to a concrete binding/lane selected before the call. A saturated lane is removed from candidate selection; it must not poison the entire ProviderDispatcher.

- [ ] **Step 4: Add durable saturation wait and capacity wake**

  Preserve existing queue/state ownership. On saturation, store the binding-specific wait reason and do not consume logical execution retry budget. On future completion or startup reconciliation, wake only matching tasks once, bounded by the existing queue API.

- [ ] **Step 5: Handle late completion**

  Re-evaluate the durable provider intent on late completion. Known success/confirmed failure continues the existing lifecycle without a new Provider request; unknown remains reconciliation-gated.

- [ ] **Step 6: Run focused scheduler/reconciliation tests and commit**

  ```powershell
  python -m pytest -q tests/v2/test_provider_saturation_and_wake.py tests/v2/test_phase6_provider_reconciliation.py tests/v2/test_phase6_scheduler.py
  git add src/dev_agent/runtime/model_turn.py src/dev_agent/runtime/controller.py src/dev_agent/providers/dispatcher.py src/dev_agent/scheduler/queue.py src/dev_agent/scheduler/worker.py src/dev_agent/operation.py src/dev_agent/runtime/provider_lane.py tests/v2/test_provider_saturation_and_wake.py tests/v2/test_phase6_provider_reconciliation.py
  git commit -m "fix: isolate provider saturation and wake parked tasks"
  git push origin v2/bootstrap
  ```

### Task 7: Fence AgentBackend start and event persistence

**Files:**
- Modify: `src/dev_agent/backends/dispatcher.py`
- Modify: `src/dev_agent/state/effects_repository.py`
- Modify: `src/dev_agent/state/sqlite_store.py`
- Test: `tests/v2/test_agent_backend_race.py`
- Test: `tests/v2/test_agent_backend_dispatcher.py`

**Interfaces:**
- Consumes: existing effect intent lifecycle, `BackendAdmission`, `BackendDispatchUncertain`, `AgentBackendDispatcher`, and SQLite transaction ownership.
- Produces: atomic `pending/prepared → dispatching` claim bound to one owner token; one external `backend.start()` per `dispatch_id`; durable event dedupe by dispatch/sequence.

- [ ] **Step 1: Add deterministic race tests**

  Coordinate two dispatchers and two StateStore connections with barriers. Assert one `backend.start()` call, a durable session or typed uncertainty, and no `NameError` on the race path. Add a concurrent event poll test for one sequence.

- [ ] **Step 2: Run RED**

  Run `python -m pytest -q tests/v2/test_agent_backend_race.py` and confirm the race reproduces as duplicate start, invalid transition, or the old undefined exception.

- [ ] **Step 3: Implement atomic effect-intent claim**

  Use the existing transaction owner and expected status predicate. Store an owner/fencing token in the intent result. Only the successful claimant calls `backend.start()`; losers reread durable state and return the session or raise `BackendDispatchUncertain`.

- [ ] **Step 4: Make session-persist crash recoverable**

  Preserve `client_session_key` discovery. If start receipt persistence fails, restart uses discovery/reconciliation and never calls start again for the same identity.

- [ ] **Step 5: Make event insert atomic**

  Add a durable uniqueness check or transactional insert for `(dispatch_id, sequence)` while preserving existing conflict detection and event payload semantics.

- [ ] **Step 6: Run focused backend tests and commit**

  ```powershell
  python -m pytest -q tests/v2/test_agent_backend_race.py tests/v2/test_agent_backend_dispatcher.py tests/v2/test_agent_backend_protocol.py
  git add src/dev_agent/backends/dispatcher.py src/dev_agent/state/effects_repository.py src/dev_agent/state/sqlite_store.py src/dev_agent/state/schema.py tests/v2/test_agent_backend_race.py tests/v2/test_agent_backend_dispatcher.py
  git commit -m "fix: fence concurrent agent backend dispatch"
  git push origin v2/bootstrap
  ```

### Task 8: Formalize UNKNOWN quota and ADR evidence

**Files:**
- Create: `spec/v2/adr/ADR-012-unknown-quota-operation.md`
- Modify: `src/dev_agent/resources/router.py`
- Modify: `src/dev_agent/operation.py`
- Modify: `src/dev_agent/scheduler/quota.py`
- Test: `tests/v2/test_quota_policy.py`
- Test: `tests/v2/test_quota_scheduler.py`

**Interfaces:**
- Consumes: current trusted billing catalog, quota observation, `QuotaWakeScheduler`, `QuotaRequalificationCoordinator`, and `allow_unknown_quota` route admission.
- Produces: explicit `UNKNOWN_QUOTA` behavior: trusted current no-charge and qualified bindings may use local bounded admission; remaining/reset stay unknown; 429 blocks and requires bounded requalification.

- [ ] **Step 1: Write the failing repeat-admission test**

  Assert that unknown telemetry does not fabricate `remaining` or `reset_at`, that trusted current free bindings follow the documented bounded local policy, and that known blocked/expired billing cannot enter bootstrap.

- [ ] **Step 2: Run RED**

  Run `python -m pytest -q tests/v2/test_quota_policy.py tests/v2/test_quota_scheduler.py -k "unknown or bootstrap"`.

- [ ] **Step 3: Write ADR-012 before changing semantics**

  Record the distinction between provider quota and local bounded admission, the 429 block transition, the conservative unknown billing rule, and the fact that “unknown” is not “available”.

- [ ] **Step 4: Implement the smallest policy change**

  Keep existing Resource and Budget authorities. Use local bounded admission only when exact binding/model qualification, trusted no-charge billing, health, and activation are current. Never write a guessed quota value.

- [ ] **Step 5: Run quota focused tests and commit**

  ```powershell
  python -m pytest -q tests/v2/test_quota_policy.py tests/v2/test_quota_scheduler.py tests/v2/test_operation.py
  git add spec/v2/adr/ADR-012-unknown-quota-operation.md src/dev_agent/resources/router.py src/dev_agent/operation.py src/dev_agent/scheduler/quota.py tests/v2/test_quota_policy.py tests/v2/test_quota_scheduler.py
  git commit -m "docs: define bounded unknown quota operation"
  git push origin v2/bootstrap
  ```

### Task 9: Compose Operation with the existing finite lifecycle

**Files:**
- Modify: `src/dev_agent/operation.py`
- Modify: `src/dev_agent/intelligence/coordination.py`
- Modify: `src/dev_agent/intelligence/lifecycle_loop.py`
- Modify: `src/dev_agent/intelligence/execution.py`
- Test: `tests/v2/test_operation_hierarchy_e2e.py`
- Test: `tests/v2/test_intelligence_dispatch_loop.py`

**Interfaces:**
- Consumes: `EvaluationCoordinator`, `EvaluationDispatchCoordinator`, `EscalationExecutor`, `TaskLifecycleCoordinator`, `FiniteLifecycleLoop`, and the existing Operation Worker loop.
- Produces: normal `OperationService` execution that enters the existing finite evaluation/review/dispatch lifecycle; no Operation-specific retry state machine.

- [ ] **Step 1: Add a full Operation composition test**

  Use fake L1 primary/alternate/L2 bindings and a deterministic host evaluator. Submit through `OperationService`, force a known retryable L1 result, record same-tier fallback, require explicit reviewed escalation, then assert terminal state and durable audit.

- [ ] **Step 2: Run RED**

  Run `python -m pytest -q tests/v2/test_operation_hierarchy_e2e.py` and confirm the existing Operation path does not enter `FiniteLifecycleLoop` or the coordinator.

- [ ] **Step 3: Add a thin composition hook**

  Construct the existing coordinators from Operation-owned StateStore/ResourceControlPlane dependencies. Keep Controller responsible for lifecycle/checkpoint composition and keep higher-tier dispatch behind the current review contract.

- [ ] **Step 4: Preserve UNKNOWN and waiting states**

  Ensure Operation never turns unknown external outcome into retry/escalation. Ensure queue claim, execution attempt, evaluation cycle, and escalation counts remain bounded by their existing authorities.

- [ ] **Step 5: Run Operation and lifecycle focused tests and commit**

  ```powershell
  python -m pytest -q tests/v2/test_operation_hierarchy_e2e.py tests/v2/test_operation.py tests/v2/test_intelligence_dispatch_loop.py tests/v2/test_intelligence_lifecycle_loop.py
  git add src/dev_agent/operation.py src/dev_agent/intelligence/coordination.py src/dev_agent/intelligence/lifecycle_loop.py src/dev_agent/intelligence/execution.py tests/v2/test_operation_hierarchy_e2e.py
  git commit -m "feat: connect operation to finite intelligence lifecycle"
  git push origin v2/bootstrap
  ```

### Task 10: Add a bounded root planning proposal

**Files:**
- Create: `src/dev_agent/intelligence/planner.py`
- Modify: `src/dev_agent/operation.py`
- Modify: `src/dev_agent/runtime/task_graph.py`
- Test: `tests/v2/test_planner_privacy_boundaries.py`
- Test: `tests/v2/test_intelligence.py`

**Interfaces:**
- Consumes: root `Task`, existing `TaskGraph`, Task limits, sensitivity policy, and the already bounded L2 policy.
- Produces: `RootPlanningProposal` plus Host validation that creates children only through existing `submit_child()` / `TaskGraph` constraints.

- [ ] **Step 1: Define proposal-only types and failing validation tests**

  Define `RootPlanningProposal` and child proposal data as immutable data. Test that a proposal cannot directly change Task state, issue budget/approval, bypass protected assignment, or exceed child/depth/total limits.

- [ ] **Step 2: Run RED**

  Run `python -m pytest -q tests/v2/test_planner_privacy_boundaries.py -k "planner or proposal"`.

- [ ] **Step 3: Implement Host validation**

  Validate objective, task type, dependencies, execution capabilities, policy traits, risk, sensitivity, and suggested target. Apply monotonic sensitivity and existing TaskGraph cycle/depth/count checks before calling `submit_child()`.

- [ ] **Step 4: Keep planning finite and non-recursive**

  Apply one bounded proposal size and one bounded planning cycle per root. Do not allow planner output to create another planner task automatically.

- [ ] **Step 5: Run planner tests and commit**

  ```powershell
  python -m pytest -q tests/v2/test_planner_privacy_boundaries.py tests/v2/test_intelligence.py tests/v2/test_operation.py
  git add src/dev_agent/intelligence/planner.py src/dev_agent/operation.py src/dev_agent/runtime/task_graph.py tests/v2/test_planner_privacy_boundaries.py tests/v2/test_intelligence.py
  git commit -m "feat: add bounded root planning proposal"
  git push origin v2/bootstrap
  ```

### Task 11: Prove the external Worker and Host trust boundary

**Files:**
- Modify: `scripts/devfarm_worker.py`
- Modify: `scripts/devfarm_commander.py`
- Modify: `docs/DEVFARM.md`
- Modify: `docs/CODEX_COMMANDER.md`
- Modify: `docs/CURRENT_STATE.md`
- Test: `tests/v2/test_devfarm_commander.py`
- Test: `tests/v2/test_devfarm_orchestrator.py`
- Create: `.devfarm` artifacts only as ignored local evidence, never tracked source

**Interfaces:**
- Consumes: existing Commander plan, DevFarm provider eligibility, Host Verification, metrics, and explicit integration proof.
- Produces: 2–3 non-overlapping real L1 Worker tasks routed through Commander, Host Verified, reviewed, Git-integrated, and metric-recorded; trust level remains `HOST_CONTAINED` unless OS sandbox evidence exists.

- [ ] **Step 1: Create two narrow manifests**

  Use distinct file ownership, explicit outbound scope, exact qualified provider/model/binding, bounded tests, and no protected paths. Do not assign the same file to two Workers.

- [ ] **Step 2: Dispatch through Commander**

  Run the existing plan/dispatch/collect flow with real qualified Cloud Worker activation. Do not use model self-reported tests as evidence.

- [ ] **Step 3: Host verify and integrate explicitly**

  Apply proposals only to isolated worktrees, run approved host tests, review diffs, and record `target_ref`, `integration_revision`, `source_attempt_id`, and patch digest. Official branch mutation remains Codex-controlled.

- [ ] **Step 4: Inspect metrics**

  Verify provider/binding/model/tier, task type, elapsed, usage, host result, retry, accepted/rejected, and correction size. Keep raw credentials and source secrets out of artifacts.

- [ ] **Step 5: Document external limits**

  Keep `HOST_CONTAINED`, not `OS_SANDBOXED`, until filesystem/network isolation is directly demonstrated. Record GitHub ruleset as external status if not configured.

- [ ] **Step 6: Run focused DevFarm tests and commit**

  ```powershell
  python -m pytest -q tests/v2/test_devfarm_commander.py tests/v2/test_devfarm_orchestrator.py tests/v2/test_devfarm_manifest.py tests/v2/test_devfarm_patch_validation.py
  git add scripts/devfarm_worker.py scripts/devfarm_commander.py docs/DEVFARM.md docs/CODEX_COMMANDER.md docs/CURRENT_STATE.md tests/v2/test_devfarm_commander.py tests/v2/test_devfarm_orchestrator.py
  git commit -m "test: prove commander cloud worker integration"
  git push origin v2/bootstrap
  ```

### Task 12: Full verification, evidence, and Gate reclassification

**Files:**
- Modify: `docs/CURRENT_STATE.md`
- Modify: `docs/SYSTEM_MAP.md`
- Modify: `docs/CODEX_COMMANDER.md`
- Modify: `docs/DEVFARM.md`
- Modify: `spec/v2/05_api_spec.md`
- Modify: `spec/v2/06_implementation_spec.md`
- Modify: `spec/v2/TRACEABILITY.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/requirements/autonomous-hierarchy-hardening/06-roadmap-tests-acceptance.md` only to mark verified evidence
- Do not modify: `spec/v2/GATE_STATUS.json` without separately verified Gate evidence

**Interfaces:**
- Consumes: all focused slices, local regression, external CI, and real Worker artifacts.
- Produces: truthful Current State and Traceability; Phase 7 Gate is only closed for requirements with direct evidence, while paid Provider, GitHub ruleset, and OS sandbox remain external/deferred when applicable.

- [ ] **Step 1: Run full local regression**

  ```powershell
  python -m pytest -q tests/v2 --durations=10
  python -m compileall -q src
  python scripts/check_gate.py
  ```

  Record pass count, skip reason, and the expected remaining external Gate result.

- [ ] **Step 2: Run exact-head CI**

  Push the verified commit, then inspect `v2-core` Python 3.10/3.11 and `v2 tests` for the exact commit SHA. Do not write run IDs back into the commit being tested as self-certification.

- [ ] **Step 3: Update docs and traceability**

  Mark each requirement `PLANNED`, `PARTIAL`, `VERIFIED`, or `BLOCKED_EXTERNAL` based on direct tests/evidence. Keep the requirements bundle as the scope document and Current State as the implementation truth.

- [ ] **Step 4: Perform final clean-tree check and commit**

  ```powershell
  git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" diff --check
  git -c "safe.directory=C:/Users/kinok/Documents/Programs/dev_agent" status --short --branch
  git add docs/CURRENT_STATE.md docs/SYSTEM_MAP.md docs/CODEX_COMMANDER.md docs/DEVFARM.md spec/v2/05_api_spec.md spec/v2/06_implementation_spec.md spec/v2/TRACEABILITY.md CHANGELOG.md
  git commit -m "docs: record autonomous hierarchy hardening evidence"
  git push origin v2/bootstrap
  ```

## Plan self-review

- Coverage: A1–A4 are covered by Tasks 1–2; B1–B4/C1–C3 by Tasks 1, 6, and 7; D1–D3/E1/F1/I2 by Tasks 3–5 and 8; G1–G2 by Tasks 9–10; H1/I1/J1/J2 by Task 11; formal synchronization and final acceptance by Task 12.
- No new production Scheduler, StateStore, Budget, Agent framework, MCP, or Codex adapter is introduced.
- Existing Gate status is never promoted from a test artifact alone; external blockers remain explicit.
- Task interfaces use existing classes and the newly defined `QualificationProjection`, `RootPlanningProposal`, and binding-lane boundaries only where a focused failing test demonstrates the need.
- Every production task has a RED test, focused GREEN verification, full regression checkpoint, and commit/push step.
