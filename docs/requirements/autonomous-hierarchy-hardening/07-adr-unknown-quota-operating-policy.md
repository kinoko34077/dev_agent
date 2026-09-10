# ADR: UNKNOWN quota operating policy

Status: Accepted for the current Phase 7 hardening slice

## Context

Some qualified, no-charge provider bindings do not expose usable quota
telemetry. Treating that absence as remaining capacity would fabricate quota
evidence; rejecting the binding forever would make a verified free provider
unusable. A provider quota domain is also shared by credentials and must not
be multiplied by counting keys separately.

## Decision

An unknown quota observation is a distinct state. It is neither available nor
blocked. A request may enter an UNKNOWN quota lane only when all of the
following are true:

- the exact provider binding and model are currently qualified;
- the trusted billing authority proves the binding is no-charge;
- the resource is healthy or degraded and not circuit-open;
- the request explicitly allows unknown-quota admission; and
- the quota domain is known.

The ResourceLedger applies a durable, domain-keyed local admission window. The
current default is one admission per quota domain per 60 seconds. This is a
local safety limit, not provider telemetry, and it never creates a `remaining`
or `limit` value. A slot is returned only when the provider boundary was not
crossed; after an external attempt it remains consumed for the local window.

The provider response remains authoritative:

- success without quota telemetry refreshes liveness but leaves quota
  UNKNOWN;
- a quota/rate-limit response records the existing domain block and routes the
  task through bounded requalification;
- authorization or permission failures are not converted into quota recovery;
- an expired or untrusted billing/qualification fact cannot use this lane.

Due local windows wake only tasks carrying the matching
`quota_unknown:<domain>` reason. Operation maintenance may perform this
bounded wake; it does not poll providers or invent a quota observation.

## Consequences

The same free provider can be used again after the local window, but unknown
quota does not become an unlimited fallback. Multiple credentials in one
domain share the admission counter. Provider-specific quota parsing remains in
the adapter and existing `QuotaRequalificationCoordinator` remains the
recovery authority.

The default window and admission count are policy defaults and may be tightened
by a protected operator change. They are not a substitute for live provider
qualification or paid-budget accounting.

## Evidence

- `ResourceLedger` schema v9 stores `quota_unknown_admissions`.
- `ResourceControlPlane` claims the domain admission before reservation and
  releases it only before an external boundary is crossed.
- `OperationService.maintenance_tick()` wakes due unknown-quota tasks without
  probing when no provider callback is supplied.
- Focused tests cover migration, domain separation, expiry, and denial after
  the local admission is consumed.
