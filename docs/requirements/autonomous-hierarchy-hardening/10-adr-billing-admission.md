# ADR: Billing admission for allowance-backed resources

Status: Accepted for the current hardening slice

## Decision

Billing identity is resolved by exact provider, binding, and model. Billing
mode is distinct from the numeric reservation amount:

- `free_fixed` may be admitted as zero cost only with current trusted
  authority;
- `recurring_allowance` is zero-cost routable only when the trusted profile is
  explicitly `hard_stop` and guarantees no billable overage;
- `recurring_credit` is a finite credit resource and is not a zero-cost
  shortcut;
- `paid` requires a known price and normal budget admission;
- `unknown` is fail-closed.

Missing response cost is settled as zero only when the durable reservation
contains the trusted no-charge guarantee and its billing evidence. A
`cost_minor=0` field by itself is not billing authority.

Qualification, billing, privacy, and operator activation remain separate
authorities. They meet only in the effective resource eligibility decision.

## Consequences

Allowance-backed providers cannot silently bypass hard budget when provider
telemetry omits a price. A reviewed hard-stop allowance can remain in the
free-first pool, while a billable or ambiguous allowance is blocked until a
price/headroom authority is available.
