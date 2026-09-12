# G6O1 simulated-paid / live billing split

## Status

This document splits the evidence scope of G6O1. It does not change
`spec/v2/GATE_STATUS.json`: G6O1 remains externally blocked until the live
paid-provider and deployment-owned budget conditions are satisfied.

## G6O1-SIM

G6O1-SIM uses a separately identified simulated-paid binding. The network call
may reach a free provider or an independent free service, but the resource
authority records a virtual price and a billing mode that is not `free_fixed`.
The fact that the external call happens to cost zero does not erase the virtual
charge or convert an unknown outcome to zero.

The existing `ResourceControlPlane`, budget reservation, provider effect intent,
durable audit, and reconciliation boundaries remain authoritative. No new
scheduler, budget system, or state machine is introduced.

Required evidence:

1. normal virtual charge and reconciliation;
2. reservation immediately before the budget boundary;
3. dispatch denial when worst-case virtual cost exceeds remaining budget;
4. timeout/server error retaining an unknown charge;
5. reservation reuse and reconciliation after restart;
6. distinct free and simulated-paid `provider_binding_id` identities.

## G6O1-LIVE

G6O1-LIVE requires a real paid provider and deployment-owned protected budget
configuration. It additionally covers actual provider charge, rounding,
minimum charge, failed-request billing, delayed billing, and external billing
evidence. These conditions cannot be proven by the local test suite alone and
remain `BLOCKED_EXTERNAL` / `DEFERRED`.

## Binding rule

At minimum, free, simulated-paid, and live-paid resources use distinct binding
identities. Runtime routing, audit, and evidence must preserve the distinction;
provider name alone is not a billing authority.
