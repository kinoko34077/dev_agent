# Phase 8 Production Composition Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the intentional Phase 8 composition RED scaffold with a thin submit/observe facade that can be composed from the existing Operation and DevFarm authorities without creating a second scheduler or hiding an incomplete execution chain.

**Architecture:** `Phase8ProductionComposition` owns only the public submission/observation boundary. The concrete submitter and observer are injected from the existing production authorities, so the facade cannot sequence private Planner, Worker, verification, review, or integration calls itself. Results are bounded mappings and no raw provider output, credentials, or conversation data cross the boundary. The next roadmap slice (#6) will bind single execution ownership behind the injected boundaries.

**Tech Stack:** Python 3.10+, pytest, existing Operation/DevFarm script boundaries, immutable/bounded mappings.

**Spec:** `docs/V2_EXECUTION_PLAN.md`, `docs/V2_DETAILED_ROADMAP.md`, `https://github.com/kinoko34077/dev_agent/issues/3`

## Global Constraints

- Preserve the existing Controller, DurableQueue, SQLiteStateStore, RuntimeCoordinator, lease/fencing, Host Verification, Review, Integration, and dependency-release authorities.
- Do not add a second scheduler, queue, state store, Agent framework, provider registry, or test-runner orchestration.
- The facade exposes exactly `submit` and `observe` as public callable methods.
- A proposal is not integration evidence; the facade must not grant review, approval, or Git mutation authority.
- Never persist or return credentials, API keys, raw provider responses, raw Discord conversations, or unbounded output.
- Keep Formal Phase 8 LIVE_ACTIVATION and D9 Production Deployment unchanged.

## Review Focus

- A non-callable submit/observe boundary must fail at construction rather than fail after a live request.
- A submitter returning an unbounded or non-mapping result must be rejected without leaking its contents.
- An observer result must be projected to bounded metadata and must not expose raw provider payloads.
- Repeated `submit` calls must remain owned by the injected durable boundary; the facade must not synthesize a second task or retry loop.
- The facade must not expose public orchestration methods beyond `submit` and `observe`.

### Task 1: Implement the submit/observe production facade

**Files:**
- Modify: `scripts/devfarm_production_composition.py`
- Test: `tests/v2/test_phase8_production_composition_e2e.py`

**Interfaces:**
- Consumes: injected `submit_boundary(objective, **kwargs)` and `observe_boundary(run_id, **kwargs)` callables owned by existing Operation/DevFarm composition code.
- Produces: `Phase8ProductionComposition.submit(objective, **kwargs) -> dict[str, Any]` and `Phase8ProductionComposition.observe(run_id, **kwargs) -> dict[str, Any]`.

- [ ] **Step 1: Write the failing tests**

Add tests for:

```python
def test_phase8_composition_delegates_submission_and_observation_without_resequencing():
    calls = []
    composition = Phase8ProductionComposition(
        submit_boundary=lambda objective, **kwargs: calls.append(("submit", objective, kwargs)) or {"run_id": "run-1", "status": "SUBMITTED"},
        observe_boundary=lambda run_id, **kwargs: calls.append(("observe", run_id, kwargs)) or {"run_id": run_id, "status": "COMPLETED"},
    )
    assert composition.submit("one fresh root", source="test") == {"run_id": "run-1", "status": "SUBMITTED"}
    assert composition.observe("run-1", view="bounded") == {"run_id": "run-1", "status": "COMPLETED"}
    assert calls == [("submit", "one fresh root", {"source": "test"}), ("observe", "run-1", {"view": "bounded"})]


def test_phase8_composition_rejects_non_callable_boundary():
    with pytest.raises(TypeError, match="boundary"):
        Phase8ProductionComposition(submit_boundary=None, observe_boundary=lambda run_id: {})


def test_phase8_composition_rejects_non_mapping_boundary_result():
    composition = Phase8ProductionComposition(
        submit_boundary=lambda objective, **kwargs: ["raw"],
        observe_boundary=lambda run_id, **kwargs: {"run_id": run_id},
    )
    with pytest.raises(ProductionCompositionError, match="mapping"):
        composition.submit("bounded objective")
```

- [ ] **Step 2: Run the focused tests to verify the expected RED**

Run: `python -m pytest tests/v2/test_phase8_production_composition_e2e.py -q`

Expected: FAIL because `Phase8ProductionComposition` is not defined/exported and the boundary behavior is absent; the existing identity tests may still pass.

- [ ] **Step 3: Implement the minimal facade**

Add a constructor that validates both injected boundaries are callable. Implement `submit` and `observe` as strict delegation only:

```python
result = self._submit_boundary(objective, **kwargs)
if not isinstance(result, Mapping):
    raise ProductionCompositionError("submit boundary must return a mapping")
return _bounded_projection(result)
```

The bounded projection must copy only scalar/string/list/mapping values up to a fixed depth and size limit, reject non-string run IDs, and never mutate or retry the injected boundary. Keep helper names private so the public callable set remains exactly `submit` and `observe`.

- [ ] **Step 4: Run the focused tests to verify GREEN**

Run: `python -m pytest tests/v2/test_phase8_production_composition_e2e.py -q`

Expected: all Phase 8 composition tests PASS, including existing identity fail-closed cases.

- [ ] **Step 5: Run the affected regression slice**

Run: `python -m pytest tests/v2/test_development_planning_bridge.py tests/v2/test_devfarm_multirole.py tests/v2/test_phase8_production_composition_e2e.py -q`

Expected: PASS; no Operation/DevFarm ownership or identity regressions.

- [ ] **Step 6: Commit the task**

```bash
git add scripts/devfarm_production_composition.py tests/v2/test_phase8_production_composition_e2e.py docs/superpowers/plans/2026-09-25-phase8-production-composition-boundary.md
git commit -m "feat: add phase8 production composition boundary"
```
