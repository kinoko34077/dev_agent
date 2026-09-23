# ADR-015: Separate Human Proxy and Codex Expert Assist

## Status

Accepted for the local operational self-heal slice.

## Decision

The dev_agent core exposes a transport-neutral `HumanInteractionPort`. A
Human-required task is durably parked as `WAITING_HUMAN` with an exact
`request_id`; an answer is accepted only when it is correlated to that request
and is consumed once. A timeout never becomes an approval, rejection, or
default decision, and unrelated READY work continues through the existing
`RuntimeCoordinator` and `OperationService` loop.

Codex is represented by two separate adapter roles while a direct Human
transport is not yet available:

- Human Proxy requests use `HUMAN_REQUIRED` and accept only an explicitly
  correlated response from an actor identified as `human`.
- Expert Assist requests use `PROPOSAL_ONLY`; a Codex proposal cannot approve,
  integrate, mutate Git, or promote a Gate.

Both roles may use the same bounded JSON-lines transport, but they do not share
authority. The current repository provides an injectable adapter boundary; a
live external Codex MCP endpoint is not claimed until it is separately
configured and verified.

The always-on process remains the existing `RuntimeCoordinator` around
`OperationService`; no second scheduler or state store is introduced. Local
self-update composes the existing trusted revision check, pinned release,
rolling restart, health check, and last-known-good rollback. A failed candidate
revision is durably recorded beside release metadata so the same revision is
not automatically retried.

## Consequences

- Human authority is explicit and cannot be synthesized from a Codex response.
- Local format/repair work can continue without waking a Human, while
  protected authority and ambiguous external outcomes remain escalation points.
- Codex/MCP wire availability is still an external integration item.
- OS startup registration, deployed Guardian recovery, D9 Production
  Deployment, and Phase 8 LIVE_ACTIVATION remain separate deferred or
  unverified gates.

## Evidence

See [`operational-human-assist-self-update-local-20260923.json`](../evidence/operational-human-assist-self-update-local-20260923.json).
