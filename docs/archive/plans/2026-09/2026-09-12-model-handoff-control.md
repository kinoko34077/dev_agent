# Model Handoff Control Semantics Plan

## Goal

Strengthen the model-neutral handoff meaning contract used by the existing
one-cycle Planner -> Executor -> Reviewer adapter.  Preserve the separation
between control and payload, and leave Compression Service transport,
simulated-paid execution, and multi-cycle automation disconnected.

## Constraints

- Reuse `HandoffEnvelope`, roles, renderer, compression boundary, and
  `scripts/handoff_cycle.py`; do not introduce a scheduler, state machine, or
  new execution framework.
- Control is never a compression input.  New directive fields must survive
  serialization and compression-result reconstruction.
- Latest explicit correction invalidates a prior interpretation rather than
  being averaged with it.
- The existing one-cycle result still stops at the human boundary.

## Steps

1. Add a typed, JSON-safe directive value object with bounded validation for
   payload semantics, source/authority, continuation, comparison, output
   contract, exclusions, focus, and correction invalidation.
2. Add it as an optional, backwards-compatible `HandoffEnvelope` control
   field; preserve it in serialization and the existing compression client.
3. Extend the Japanese renderer without parsing rendered text as authority.
4. Add lightweight envelope-construction presets for the recurrent human
   request forms; presets do not perform repository access or analysis.
5. Pass a supplied directive through the current one-cycle request only;
   preserve the current stop boundary.
6. Add focused unit/regression tests, then synchronize the model-handoff
   requirements, Current State, execution plan, and traceability evidence.

## Verification

- Focused handoff, compression, and one-cycle tests.
- `python -m pytest tests/v2 -q`
- architecture check and `python -m compileall src scripts`
- Push only after local evidence; record external CI truthfully.
