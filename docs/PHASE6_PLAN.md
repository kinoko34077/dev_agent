# Phase 6 — Resource / Survival / Recovery

Status: VERIFIED at the current implementation boundary

Phase 6A〜6E is the current v2 work boundary. The phase is deliberately split
into small control-plane components so that resource exhaustion, provider
failure, recovery mutation, and worker ownership remain deterministic and
auditable.

## 6A — Resource Ledger and Budget Governor

Implemented in `src/dev_agent/resources/ledger.py` and `budget.py`.

- Native units remain explicit (`request`, tokens, compute time, or another
  resource unit).
- Observations persist capacity, availability, health, confidence, and time.
- Paid dispatches reserve integer minor units before dispatch.
- The normal budget cannot consume the recovery reserve.
- Unknown price fails closed.
- Provider timeout/cancellation ambiguity keeps a reservation in `unknown`
  until reconciliation.

## 6B — Router and Survival Modes

Implemented in `router.py` and `survival.py`.

- Required capability, privacy, provider allow-list, health, circuit cooldown,
  availability, and cost are evaluated before selection.
- Privacy compatibility outranks cost and latency.
- `NORMAL`, `CONSERVE`, and `SURVIVAL` are derived from a deterministic
  `SurvivalSnapshot`; the model cannot select the mode.

## 6C — Recovery operationalization

Implemented in `recovery/phase6_recovery.py` and
`recovery/validate_resources.py`.

- Recovery inspection is independent of Controller and Provider imports.
- Backup, restore, LKG, rollback planning, repair-branch planning, and
  resource-ledger validation are explicit operations.
- Restore publication and repair mutations require opt-in and maintenance
  locking.

## 6D — Scheduler and worker ownership

Implemented in `src/dev_agent/scheduler/queue.py`.

- Queue state is durable in SQLite.
- Claims are exclusive through an atomic transaction.
- `lease_owner`, `lease_until`, `state_version`, and `attempts` fence stale
  workers and support expiry/reclaim after restart.

## 6E — Kernel integration and evidence

The optional `ResourceControlPlane` is connected to Controller provider
dispatch. Existing callers remain backward-compatible when no policy is
provided. The integration reserves before every provider request, reconciles
observed usage, and preserves uncertain reservations on timeout/cancellation.

The machine-readable evidence is maintained in
`spec/v2/GATE_STATUS.json` under Stage F (`F6A`–`F6E`). The next scope is not
new Phase 6 mechanism; it is operational drills and explicitly deferred
Phase 6/7 work such as live rollback/repair execution, artifact-root retention
integration, automatic retry policy, and generated Tool lifecycle.

