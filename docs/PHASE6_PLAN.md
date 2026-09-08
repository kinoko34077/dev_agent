# Phase 6 — Resource / Survival / Recovery

Status: foundation VERIFIED; operational integration IN PROGRESS

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
- Budget policy is configured only through the explicit `BudgetAuthority`; the
  runtime `BudgetGovernor` reads persisted policy and cannot raise the hard cap.
- Budget transitions are serialized with `BEGIN IMMEDIATE`, and release/unknown
  operations validate their expected source state in the same transaction.
- Reservation state is durable as `prepared -> dispatching -> reconciled`,
  `unknown`, or `confirmed_no_charge`.

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
- Total attempts are finite: WorkerRunner uses `max_retries + 1`, while the
  Queue API defaults to three attempts when no task-specific bound is supplied.
- Tasks that return `waiting_approval`, `waiting_reconciliation`, or a
  budget/dependency block are parked as `waiting`; they are not retried until
  an explicit `DurableQueue.wake()` event.

## 6E — Kernel integration and evidence

The optional `ResourceControlPlane` is connected to Controller provider
dispatch. Existing callers remain backward-compatible when no policy is
provided. The integration reserves before every provider request, reconciles
observed usage, and preserves uncertain reservations on timeout/cancellation.
Provider timeout/transport checkpoints remain fail-closed on `resume()` until
an explicit provider reconciliation path clears the marker; cancellation of an
already ambiguous task cannot terminalize it as ordinary `cancelled`.
ProviderDispatcher and Controller-managed direct Providers also persist a
provider effect intent containing selected provider/resource and normalized
cost metadata before entering the external call; succeeded intents are replayed
without sending a duplicate request. Provider selection, estimated cost,
fallback outcome, and terminal outcome are also written to a durable audit
record instead of relying only on the dispatcher's in-memory audit list.

The lease proof for the provider intent transition is checked inside the
StateStore transaction. An independent-process test confirms that a reclaimed
queue lease prevents the stale process from entering the concrete provider.

Checkpoint JSON is exposed through `src/dev_agent/runtime/state.py:RuntimeState`;
the Controller deep-copies resumed state before mutation while retaining the
legacy mapping shape for existing checkpoints.

The machine-readable evidence is maintained in
`spec/v2/GATE_STATUS.json` under Stage F (`F6A`–`F6E`). Stage G records the
remaining operational integration: money-safe dispatch, real provider routing,
survival enforcement, lease-fenced workers, independent concurrency proof, and
recovery drills. It must be VERIFIED before Phase 6 is considered complete.
Explicitly deferred Phase 6/7 work includes qualification against a real paid
Provider, artifact-root retention integration, production-environment recovery
drills, and generated Tool lifecycle.
