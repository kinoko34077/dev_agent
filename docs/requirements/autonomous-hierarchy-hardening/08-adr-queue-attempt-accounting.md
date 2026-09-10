# ADR: Queue claim and logical execution accounting

Status: Accepted for the current Phase 7 hardening slice

## Decision

The durable queue records three different counters:

- `claim_count`: cumulative lease claims, including reclaim after a crash;
- `attempts`: consecutive claim streak used for crash-loop fencing and retained
  for compatibility with the queue API;
- `execution_attempts`: logical Controller execution attempts.

`max_attempts` remains the consecutive claim-streak ceiling. The separate
`max_execution_attempts` is the task retry ceiling. A worker binds both from
the task's finite retry limit, but they are evaluated for different reasons.

Entering any durable waiting state resets only the consecutive claim streak.
It does not increment `execution_attempts`, and waking a task does not create a
new logical retry. A completed or failed Controller execution is published
with `execution_attempt=True`; an approval, quota, budget, reconciliation, or
provider-capacity wait is published without it.

Lease fencing and crash-loop protection therefore remain durable while an
external wait can survive an arbitrary number of maintenance cycles without
burning the task's logical retry budget. An expired lease still uses the claim
streak and execution ceiling when the queue reaps it.

## Compatibility and recovery

Resource and scheduler migrations preserve the existing `attempts` value as
the initial `claim_count` and initialize logical execution accounting from the
legacy max-attempt value. Existing callers can continue to inspect
`QueueItem.attempts`; new operational status should prefer the explicit named
counters.

This accounting does not authorize replay of an UNKNOWN external effect. The
Controller/provider intent and reconciliation records remain authoritative for
whether an external request may continue.

## Evidence

- Durable queue schema v5 stores all three counters and the logical ceiling.
- Worker regression covers a task that waits, wakes, and then completes even
  when its claim streak would otherwise have reached the legacy limit.
- Recovery validation checks the new columns and finite values.
