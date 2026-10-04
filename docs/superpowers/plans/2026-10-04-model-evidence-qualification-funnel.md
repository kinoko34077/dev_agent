# Model Evidence Qualification Funnel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded, read-only model-evidence and qualification funnel report so discovered models can be filtered, measured, and selected for later qualification without granting routing authority or performing Provider calls.

**Architecture:** Reuse `ModelEvidenceCatalog`, `ModelAdmissionResolver`, `QualificationResolver`, `RuntimeAdmissionSnapshot`, and existing billing metadata. Add one pure projection module that emits per-candidate rows, per-provider/role/tier coverage, and a bounded qualification-candidate list. Extend the existing diagnostics CLI summary to expose the projection. Formal runtime admission remains owned by the existing Router/runtime authority; bootstrap observations are reported separately and never count as formal supply.

**Tech Stack:** Python 3.11, frozen dataclasses, existing `src/dev_agent/resources` contracts, `pytest`, JSON-only read-only diagnostics.

**Constraints:**

- Do not add a Provider registry, scheduler, StateStore, Router, qualification authority, billing inference, or live probe.
- Preserve `UNSPECIFIED`/explicit evidence semantics and exact `(provider, binding, model)` identity.
- Do not infer quota domains, billing, liveness, or formal tier from discovery/model names.
- Keep output bounded, deterministic, non-secret, and useful after an interrupted run.
- Use TDD: add focused failing tests before production implementation.

**Review focus:** exact-identity joins, expiry handling, independent-route counting, separation of `RUNTIME_ELIGIBLE` from `RUNTIME_BOOTSTRAP_ADMITTED`, deterministic ordering, and no accidental promotion path.

## Tasks

- [x] Add failing tests for funnel rows, evidence gaps, expiry, runtime statuses, role/tier coverage, candidate bounds, and no billing inference.
- [x] Implement the pure `model_funnel` projection using existing evidence and runtime contracts.
- [x] Expose funnel coverage and bounded qualification candidates through `diagnose_model_candidates.py --summary --json` without network access.
- [x] Run focused model-evidence tests and repair only failures (`62 passed`).
- [ ] Update Issue #72 with RED/GREEN checkpoints and the accepted scope/evidence.
- [ ] Synchronize `docs/CURRENT_STATE.md` once the slice is accepted, preserving formal Gate and external-provider blockers.
- [ ] Run the required release-boundary checks, commit, push, and verify exact-head CI.
