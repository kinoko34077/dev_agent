# G6O1 Deferred / Frozen

status: `DEFERRED_FROZEN`

verification_status: `BLOCKED_EXTERNAL` / `NOT VERIFIED`

deferred: true

roadmap_blocking: false

frozen_at: 2026-09-13

frozen_by: Human decision

## Meaning

The practical paid-provider portion of G6O1 is explicitly frozen and is not a
reason to stop the active Phase 7 development roadmap. This is a roadmap
decision, not a verification result. The canonical Gate record remains
`spec/v2/GATE_STATUS.json`, where `G/G6O1` remains `BLOCKED` and no evidence
has been promoted.

## Deferred scope

- real paid-provider qualification and worst-case billing evidence;
- deployment-owned protected budget configuration and its external protection;
- G6O1-SIM simulated-paid runtime E2E, including reservation, UNKNOWN charge,
  reconciliation, and restart evidence;
- G6O1-LIVE billing behavior such as rounding, minimum charge, delayed charge,
  and failure-time billing.

The original requirements remain authoritative in
`docs/requirements/model-handoff/03-compression-and-g6o1.md`, the split-gate
record `spec/v2/G6O1_SIM_LIVE.md`, and
`spec/v2/adr/ADR-012-g6o1-sim-live.md`. This companion does not remove or
rewrite those acceptance criteria.

## resume trigger

Resume only when a human explicitly reopens G6O1, paid-provider production
operation becomes necessary, or deployment-owned paid budget evidence is
required for a release decision.

## Resume procedure

1. Re-read the canonical G6O1 requirements and current Gate record.
2. Reconfirm provider, billing, deployment, and budget conditions.
3. Refresh qualification and billing evidence with current timestamps.
4. Run the applicable G6O1-SIM and/or G6O1-LIVE acceptance tests.
5. Reconcile reservations, UNKNOWN outcomes, restart behavior, and external
   billing evidence.
6. Reevaluate the Gate with a new evidence revision; do not infer promotion
   from this deferred record.
