# ADR-012: Separate G6O1-SIM from G6O1-LIVE

## Context

G6O1 combines code-verifiable budget/reconciliation behavior with external
proof of real paid-provider billing. Treating the two as one local test gate
either blocks safe free development or incorrectly promotes a local simulation
as paid-provider evidence.

## Decision

Define `G6O1-SIM` for simulated-paid bindings with explicit virtual billing and
`G6O1-LIVE` for real paid-provider/deployment-owned evidence. Keep separate
binding identities and preserve the existing ResourceControlPlane, budget,
effect intent, audit, and reconciliation owners. A free external response does
not turn a simulated-paid or unknown charge into zero.

`G6O1-SIM` may be verified with code and controlled communication. `G6O1-LIVE`
remains `BLOCKED_EXTERNAL` until its external conditions are met.

## Consequences

- Free-first Phase 7 development can continue without weakening G6O1.
- Simulated billing is explicit and cannot be confused with a free binding.
- A later Compression Service dogfood can use G6O1-SIM without adding a second
  budget or scheduler implementation.
