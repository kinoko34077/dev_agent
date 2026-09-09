# Phase 6 — Resource / Survival / Recovery

Status: foundation VERIFIED; G6O2〜G6O6 VERIFIED; G6O1 BLOCKED_EXTERNAL; Phase 6A quota/provider expansion locally verified

The earlier Phase 6 operational code evidence baseline is
`da74b3b934cde66060ecabe65916fb57442b5bd9` (external exact-head CI:
v2-core run `34311452342`, v2 tests run `34311452341`). The current Phase 6A
code baseline is `0d690d888b58574b721572b81c16de20ee324066`; its external
exact-head CI is v2-core run `34318901211` and v2 tests run `34318901190`.
CI run IDs are external observations, not repository self-certification
records. The documentation commit that records them is not itself presented
as code evidence.

Phase 6A〜6E is the current v2 work boundary. The phase is deliberately split
into small control-plane components so that resource exhaustion, provider
failure, recovery mutation, and worker ownership remain deterministic and
auditable.

Next-stage requirements are indexed at docs/requirements/README.md. The
current Phase 6A boundary adds durable `quota_domain` and quota observations,
operational resource observations, fresh quota-aware routing, and normalized
quota telemetry ingestion from a valid provider response. Shared domains use
conservative fresh headroom rather than summing credentials; concurrency
limits are hard filters before dispatch. Groq, Cloudflare Workers AI, Mistral,
and OpenRouter Free are separate normalized Adapter contracts. Groq and
Cloudflare also have opt-in standard-library HTTP adapters; live qualification
and provider-specific qualification evidence remain later work. Phase
7A/B also carries a typed Task profile and deterministic intelligence-policy
metadata into ModelRequest; it does not select a model or add an AgentBackend.
The canonical path stays
Controller -> ProviderDispatcher -> ProviderRegistry -> concrete Provider,
while the Controller direct-provider branch remains compatibility-only. It
does not implement model-tier routing, Hedging, AgentBackend, MCP, evaluator
promotion, or later Phase 7 stages.

## 6A — Resource Ledger and Budget Governor

Implemented in `src/dev_agent/resources/ledger.py` and `budget.py`.

- Native units remain explicit (`request`, tokens, compute time, or another
  resource unit).
- Observations persist capacity, availability, health, confidence, and time.
- Resource identities may declare a `quota_domain`; quota observations are
  durable and schema-migrated, with stale/missing domain observations rejected
  by the quota-aware Router.
- Current operational observations include quota headroom/reset, latency and
  failure EWMA, inflight, and concurrency limit. Adapters may expose only the
  provider-neutral `usage.quota_observation` object; malformed auxiliary
  telemetry is ignored without invalidating an otherwise valid model result.
- Paid dispatches reserve integer minor units before dispatch.
- The normal budget cannot consume the recovery reserve.
- Unknown price fails closed.
- Provider timeout/cancellation ambiguity keeps a reservation in `unknown`
  until reconciliation.
- Budget policy is configured only through the explicit `BudgetAuthority`; the
  runtime `BudgetGovernor` reads persisted policy and cannot raise the hard cap.
- Deployments can expose `BudgetAuthority.configure_from_protected_file()` to
  an operator supervisor using a config path outside the Agent workspace; the
  runtime has no file-write path, while OS ACL/Secret Store protection of that
  external path remains a deployment prerequisite. The loader rejects
  symlinked config files, type coercion, and group/world-writable files on
  POSIX; Windows ACL verification remains deployment-owned.
- Recovery Reserve is not a caller-controlled boolean: direct
  `recovery=True` reservations are rejected, and only
  `BudgetAuthority.reserve_recovery(governor, recovery_task, ...)` can use a
  `RecoveryTaskAuthority`-created `Task(task_class="recovery")`; trusted
  StateStore rehydration preserves that authority without exposing it in JSON.
- Budget transitions are serialized with `BEGIN IMMEDIATE`, and release/unknown
  operations validate their expected source state in the same transaction.
- Reservation state is durable as `prepared -> dispatching -> reconciled`,
  `unknown`, or `confirmed_no_charge`.
- Provider reservations can be bound to a durable effect-intent key. A restart
  after the budget transition but before the intent transition reuses the
  existing reservation instead of creating a second charge hold.
- The isolated live qualification script exercises the full local Ollama path
  through Controller, ProviderDispatcher, ResourceRouter, Budget, concrete
  HTTP provider, usage reconciliation, and durable provider audit. Its
  recorded evidence is kept under `spec/v2/evidence/`; paid-provider
  worst-case qualification remains a separate operational gate.
- `scripts/qualify_phase6_paid_provider.py` is a fail-closed operator entry for
  that separate gate. It requires both an explicit billing flag and an exact
  confirmation phrase, reads budget policy only from an external protected
  path, and refuses to promote a response that lacks provider-reported
  `usage.cost_minor`; such a run remains reconciliation-required.
- A zero-priced resource may reconcile a legacy response with omitted usage as
  zero because its protected price is already no-charge; a paid reservation
  with omitted or malformed `usage.cost_minor` is held as unknown and cannot
  complete the dispatch.

## 6B — Router and Survival Modes

Implemented in `router.py` and `survival.py`.

- Required capability, privacy, provider allow-list, health, circuit cooldown,
  availability, and cost are evaluated before selection.
- Privacy compatibility outranks cost and latency.
- When a Resource declares a quota domain, fresh positive quota headroom is
  required and preferred before effective cost; missing, future, or stale
  observations are ineligible.
- `NORMAL`, `CONSERVE`, and `SURVIVAL` are derived from a deterministic
  `SurvivalSnapshot`; the model cannot select the mode.

## 6C — Recovery operationalization

Implemented in `recovery/phase6_recovery.py` and
`recovery/validate_resources.py`.

- Recovery inspection is independent of Controller and Provider imports.
- `recovery/validate_resources.py` and `recovery/validate_queue.py` validate
  current resource/scheduler schema versions and binding/lease invariants
  without importing the runtime.
- Backup, restore, LKG, rollback planning, repair-branch planning, and
  resource-ledger validation are explicit operations.
- Restore publication and repair mutations require opt-in and maintenance
  locking.
- `scripts/phase6_recovery_drill.py` runs the complete operator-equivalent
  sequence in a temporary Git/SQLite environment without touching the source
  checkout; the retained summary is under `spec/v2/evidence/`.

## 6D — Scheduler and worker ownership

Implemented in `src/dev_agent/scheduler/queue.py`.

- Queue state is durable in SQLite.
- Queue schema migrations are ordered through v3; `max_attempts` is persisted
  per item so lease expiry and worker crash recovery cannot retry forever.
- Claims are exclusive through an atomic transaction.
- `lease_owner`, `lease_until`, `state_version`, and `attempts` fence stale
  workers and support expiry/reclaim after restart.
- Total attempts are finite: WorkerRunner uses `max_retries + 1`, while the
  Queue API defaults to three attempts when no task-specific bound is supplied.
- Tasks that return `waiting_approval`, `waiting_reconciliation`, or a
  budget/dependency block are parked as `waiting`; they are not retried until
  an explicit `DurableQueue.wake()` event.

## 6E — Kernel integration and evidence

The normal multi-provider path is
Controller -> ProviderDispatcher -> ProviderRegistry -> concrete Provider.
The optional `ResourceControlPlane` is connected to that dispatcher boundary.
Existing direct ModelProvider callers remain backward-compatible when no
policy is provided; when a policy is supplied, the Controller-managed direct
branch is explicitly a compatibility/legacy path. The integration reserves
before every provider request, reconciles
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
The budget reservation is keyed by that same provider intent, so the two
durable records remain replay-compatible across the crash boundary between
reservation and dispatch-intent persistence.

Task requests carry typed `task_type`, `risk`, and `required_capabilities`.
`TaskIntelligencePolicy` derives bounded minimum and maximum tiers without
reading a model-selected tier from task metadata; the decision is included in
normalized request metadata for audit and later Evaluator integration.

Phase 6A's additional free-provider adapters (Groq, Cloudflare Workers AI,
Mistral, and OpenRouter Free) expose the normalized contract. Groq and
Cloudflare HTTP adapters keep endpoint/auth/response details inside their
provider modules, while their live qualification remains opt-in. Contract,
HTTP-decoder, and normalized quota-ingestion tests do not constitute live
provider or quota qualification evidence.

The lease proof for the provider intent transition is checked inside the
StateStore transaction. An independent-process test confirms that a reclaimed
queue lease prevents the stale process from entering the concrete provider.
The provider boundary is also checked after the concrete call returns: if the
worker loses its lease, the response and charge-bearing reservation remain
unknown and the task requires reconciliation. Loss of durable success/audit
persistence after an accepted response is handled the same way rather than as
a local provider decode failure.

Checkpoint JSON is exposed through `src/dev_agent/runtime/state.py:RuntimeState`;
the Controller deep-copies resumed state before mutation while retaining the
legacy mapping shape for existing checkpoints. The active model request ID is
also checkpointed, so a crash after provider dispatch cannot silently create a
new external request on resume.

The machine-readable evidence is maintained in
`spec/v2/GATE_STATUS.json` under Stage F (`F6A`–`F6E`) and Stage G
(`G6O1`–`G6O6`). G6O2–G6O6 are verified within their stated operational
responsibilities; G6O1 remains the final gate for paid worst-case dispatch and
deployment-owned budget administration. All Stage G records must be VERIFIED
before Phase 6 is considered complete.
Explicitly deferred Phase 6/7 work includes qualification against a real paid
Provider, production-environment recovery drills with retained artifacts,
actual model-tier routing, independent Evaluator persistence, and generated
Tool lifecycle. Artifact-root backup/restore is implemented through
the RecoveryOperator, but its production retention policy remains operator work.
