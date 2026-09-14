# ADR-013: Separate Process Coordination from the Task Plane

## Context

Agent and Codex need to survive conversation/process boundaries without
turning the existing Commander, Scheduler, Recovery, or Operation layers into
a second process manager. A task plan describes work ownership and dependency;
it does not provide a durable peer identity, mailbox, or restart generation.

## Decision

Introduce a development/runtime foundation under `src/dev_agent/coordination/`
with three explicit responsibilities:

1. typed peer records with `role`, `instance_id`, and monotonic generation;
2. a separate SQLite store for peer presence and at-least-once mailbox delivery;
3. immutable, content-addressed handoff artifacts carrying SHA-256, size, kind,
   and revision provenance.

`ProcessCoordinationService` is a thin composition boundary. It does not own
Task scheduling, provider routing, budget, approval, Host Verification,
integration, process creation, or rollback. Commander remains responsible for
cross-plan task ownership; its active ownership check prevents two unfinished
plans from claiming overlapping files.

Guardian, OS service integration, graceful drain, revision-pinned runtime,
rolling restart, and D9 real mutation are deferred until the corresponding
durable control and fault evidence exists. No external session discovery is
inferred from stale artifacts or thread identifiers.

## Consequences

- A Codex or Agent restart can reconstruct presence and unread handoffs from a
  separate durable store without keeping raw conversation as memory SSOT.
- Stale generations and duplicate delivery are explicit, testable outcomes.
- Task and process lifecycles remain independently inspectable and recoverable.
- A future Guardian can consume these records without granting an LLM direct
  process-control authority.
- The foundation adds no daemon, scheduler, provider call, or production
  self-repair capability by itself.
