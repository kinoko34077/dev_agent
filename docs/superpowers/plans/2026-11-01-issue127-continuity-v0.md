# Issue #127 continuity V0 — evidence boundary and metrics stability

## Goal

Revalidate the continuation roadmap from the live `v2/bootstrap` head using
read-only evidence, repair any reproducible defect in the evidence path, and
record a reviewable V0 matrix without changing Phase 8 authority or external
provider configuration.

## Scope and ownership

- Codex owns the cross-cutting evidence map, the metrics fix, regression
  coverage, Issue/PR evidence, and final integration review.
- No Worker is assigned: the defect is in the shared metrics/evidence boundary
  and requires cross-cutting validation.
- Existing Commander, DevFarm, Supervisor, Host Verification, routing,
  Recovery, and Gate boundaries remain unchanged.

## Execution slices

1. Reproduce the nullable `intelligence_tier` ordering failure against a
   focused fixture and retain the failing test as the regression contract.
2. Apply the smallest production fix that normalizes only the sort key while
   preserving nullable tier values in returned evidence.
3. Run focused metrics/routing tests and the complete `tests/v2` regression;
   re-read the real `.devfarm/metrics.sqlite3` in read-only mode.
4. Add the Issue #127 V0 evidence matrix with actual paths, evidence class,
   missing seam, owner, and first executable next action. Keep formal Phase 8
   status unchanged.
5. Commit, push, review, merge, and revalidate exact-head CI before recording
   the durable checkpoint in Issue #127 and `CURRENT_STATE.md`.

## Exit criteria

- Mixed nullable and string tiers no longer crash `WorkerMetricsStore.summarize`.
- Routing decisions are observable from the real metrics database without
  mutating it.
- The V0 matrix is traceable to current HEAD, tests, Drive source principles,
  and Issue #127, with unresolved V1+ work explicitly preserved.
- No paid generation, credential/permission/Gate mutation, or external effect
  replay occurs.
