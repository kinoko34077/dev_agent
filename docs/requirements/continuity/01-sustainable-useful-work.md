# Issue #127: Sustainable useful work under model uncertainty

Status: accepted requirement direction; implementation must be verified by the
acceptance matrix below. Issue #127 owns the cross-cutting delivery and evidence
until its acceptance conditions are met. Formal Phase 8 remains owned by Issues
#65 and #11.

## 1. Outcome

The system must increase independently checked, user-goal-relevant completed
work over time despite limited or unavailable models, Providers, quota, and
compute. Uptime, calls, retries, candidate counts, and model self-reports are
not useful-work outcomes by themselves.

## 2. Separate four decisions

For each exact task and execution identity, keep these decisions independent:

1. **Execution safety and authority:** exact binding, credentials and
   permissions, billing/no-charge, budget, privacy/egress, quota, health,
   circuit state, and effect/reconciliation state.
2. **Technical compatibility:** the exact model and adapter can perform the
   required text, structured-output, tool, or other interface contract.
3. **Task quality estimate:** expected success for this task, with evidence
   provenance and freshness. Missing or expired performance evidence means
   `QUALITY_UNVERIFIED`; it does not mean `MODEL_INCAPABLE`.
4. **Output acceptance:** deterministic checks, Host Verification, risk-based
   review, explicit integration authority, and any Human Gate.

Success at one decision must not grant another. In particular, a routeable
weak-quality model may return only an untrusted proposal; routing does not
accept its output, certify a production tier, or satisfy a protected Gate.

## 3. Performance evidence is advisory

Benchmark score, benchmark-derived Tier, Task Fit, and the presence, confidence,
or expiry of those performance observations may affect candidate ordering,
task size, decomposition, verification intensity, independent review, and
finite escalation. None is a hard execution filter by itself for otherwise
safe, technically compatible, bounded proposal work.

The Router may use available Task Fit or independently verified outcomes to
rank safe candidates. Missing Task Fit is unknown, not a zero capability. A
low score may select a smaller task or stronger checks; it must not remove every
safe proposal path solely because of the score. A caller's explicit hard
execution constraints remain binding, but benchmark-derived Tier must not be
used to satisfy or contradict those constraints.

## 4. Hard boundaries

The following remain hard for the affected action:

- exact current model and binding identity; no silent alias or substitution;
- actual required interface support and current exact remote qualification;
- credentials, permissions, privacy/egress, budget, and trusted no-charge
  authority;
- known quota exhaustion, quarantine, health/circuit state, and request limits;
- unknown external effects and reconciliation-required requests: no replay;
- protected paths, Host Verification, explicit integration/review authority,
  and formal #65/#11 acceptance.

Missing or expired capability data does not grant a capability. The route must
prove each requested capability from the existing exact qualification or
Host-owned adapter/resource evidence; if it cannot, the task remains blocked.
An explicit current incompatibility is a hard denial. Performance evidence
does not substitute for interface evidence, and capability uncertainty does
not silently become an advertised feature.

For a bounded proposal request, a caller may mark model Tier as an advisory
preference. This can bypass only the Tier performance match: exact remote
qualification, requested capabilities, resource health, billing/quota, and
privacy checks still run. Proposal-only and Host-validation authority remain
attached to the request, so a proposal cannot grant integration or external
effect authority. Ordinary requests retain their existing explicit Tier
constraint.

## 5. Degraded execution and continuity

Use the existing Operation, ResourceRouter, Queue/Wait-Wake, ResourceLedger,
DevFarm, Commander/Supervisor, Host Verification, Reviewer, Repair, and
Recovery paths. Do not add a parallel scheduler, router, state store, budget
authority, model catalog, goal database, or approval authority.

When a task cannot advance, record a typed cause, affected scope, owner, smallest
authorized next action, and objective wake condition in its existing durable
owner. Use a genuinely eligible alternate binding, deterministic/local work,
task decomposition, or finite proposal correction when useful. A single
blocked task must not starve unrelated authorized work. Never retry unchanged
failures without a new signal, generate unrelated jobs to claim activity, or
replay an UNKNOWN external effect.

## 6. Outcome evidence

Only Host-checked results and the existing explicit acceptance/integration
record may count as accepted work. Record task class, exact binding/model,
source revision, attempt/effect identity, verification result, rework, and
accepted outcome through existing durable Task/Operation/DevFarm records.
Use these outcomes as task-specific advisory evidence only after their
provenance is verified. A model's own success claim or agreement between
correlated models is not independent evidence. Do not promote an unverified
proposal into qualification or production tier evidence.

## 7. Acceptance matrix

| Area | Required result | Evidence type |
| --- | --- | --- |
| Quality-only uncertainty | Safe, compatible bounded proposal remains selectable with missing/expired/low benchmark or Task Fit; quality state remains explicit | Router and bootstrap regression |
| Capability and authority | Unsupported interface, unknown billing, privacy violation, quota exhaustion, disallowed binding, and protected action remain denied | Negative regression at the existing Host boundary |
| Output acceptance | Low/unknown-quality output that fails contract/tests cannot be integrated or promoted | Host Verification/review/integration record |
| Continuity | Blocked task is durably parked; independent authorized work completes; wake/restart does not duplicate effects | Operation/Queue restart and concurrency regression |
| Bounded adaptation | Retry, decomposition, review, and repair stop at finite limits and respond to a concrete failure class | Attempt and recovery records |
| Real use | At least one actual low/uncertain-quality model-assisted useful change reaches Host Verification and explicit acceptance | Distinct real Provider/Worker evidence; simulated tests reported separately |
| Formal acceptance | No implication that this work satisfies Phase 8 | #65/#11 remain authoritative |

