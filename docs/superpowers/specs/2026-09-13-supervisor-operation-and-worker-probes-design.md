# Supervisor Operation and Worker Probe Design

## Goal

Make the existing Codex-supervised Commander path practical for bounded
development work without creating another scheduler, state machine, or
authority layer.  Codex remains the reviewer, integrator, and roadmap
manager; qualified free Workers perform narrow implementation work.  A
separate, evidence-only probe ladder records what each Worker binding can
actually do before it is trusted with increasingly complex development
manifests.

## Scope

This design changes the development-only Supervisor composition and its
evidence/documents.  It reuses Commander plans, DevFarm manifests, Host
Verification, ReviewPacket, ReviewDecision, immutable attempts, and the
existing Host integration helper.

It does not add a production scheduler, an autonomous Planner/Reviewer,
Compression Service connectivity, paid-provider qualification, G6O1-SIM
runtime E2E, automatic push/merge/deploy, or an OS sandbox.

## Supervisor intervention boundary

`CodexSupervisedCommanderRun.advance()` stays a single bounded snapshot pass.
`run_until_intervention()` remains the blocking entrypoint and may sleep only
while a Worker outcome is actually pending.  It must return to its caller
without sleep when the durable plan needs a Codex action:

- `REVIEWING` / `review_host_verified`;
- `INTEGRATING` / `integrate_verified_worker`;
- `ACTIVE` / `rework_worker`;
- a ready Codex-owned task; and
- a Codex-resolvable Worker admission, reassignment, or rejection state.

The existing supervisor status vocabulary is retained.  A small predicate
over the current `status`, `next_action`, and plan task ownership expresses
the Codex-action boundary rather than introducing a second state machine.
`HUMAN_DECISION_REQUIRED` is reserved for actual Human Authority: a
specification or priority choice, protected authority, budget or paid-provider
change, security/privacy policy, Gate policy, or irrecoverable recovery
decision.

## Bounded wait

`max_wait_seconds` is a caller-owned process wait budget, not a Human
decision.  When it expires, the durable supervisor projection remains
`WAITING_FOR_WORKER`, records a deduplicated `SUPERVISOR_WAIT_BUDGET_EXHAUSTED`
wake, and exposes `next_action=wait_budget_exhausted` to the caller.  The
caller can schedule another bounded `run`; no Worker effect is replayed.

An overall run deadline similarly stops this invocation without asserting
Human Authority unless a separately recorded reason genuinely requires it.
Result-less expired `DISPATCHED` work retains existing orphan recovery:
collect durable results first, then classify the attempt as blocked and
reconciliation-required.  It is never blindly retried.

## Free Worker Dogfood

The first real Dogfood task is deliberately a single-file, non-protected,
one-hunk change with a pre-existing trusted verification command.  It uses
`TRUSTED_HOST_EXEC` only with explicit approval for that concrete attempt.
The default remains `STATIC_ONLY`.

The normal evidence chain is:

```
Free Worker -> valid proposal -> Host Verification -> HOST_VERIFIED
-> compact ReviewPacket -> durable ReviewDecision
-> deterministic Host integration -> INTEGRATED
```

The Host remains authoritative for changed paths, patch digest, verification,
and Git integration.  A Worker claim or a Codex success statement is not
evidence.  After one accepted task, a real or intentional quality-based
`REWORK` decision must produce a new immutable manifest with the existing
reference-first rework handoff, then reach a later Worker attempt.  Next, two
or more `CODE_INTEGRATED` dependencies prove that integration releases a
successor only on the newer integration revision.

## Worker output quality and capability probes

Existing rejection evidence is classified before changing formats: empty
proposal, malformed unified diff, and scope violation are Worker output
failures, not reasons to weaken deterministic Host validation.  The first
format response is to improve the bounded unified-diff prompt and its tests.
File replacement or structured-edit artifacts are a separate design decision
only if repeated measured failures show that JSON plus literal unified diff is
the limiting factor.

In parallel, a development-only probe ladder measures external bindings using
strictly bounded, non-secret, non-protected prompts and records the exact
provider, binding, model, request class, response validity, elapsed time,
usage if supplied, and normalized failure category.  Passing a lower probe
does not grant authority or qualification beyond its existing policy.

| Level | Example request | Purpose | Admission consequence |
| --- | --- | --- | --- |
| P0 | `SENT 'A' ONLY` | exact-output/basic transport | none |
| P1 | `3+5=?` | tiny deterministic reasoning | none |
| P2 | Python `printf` usage | concise technical answer | none |
| P3 | NumPy matrix transformation | bounded code reasoning | none |
| P4 | one explicit, narrow development action | manifest/output discipline | Dogfood candidate only |
| P5 | bounded multi-decision task | planning/follow-through observation | no autonomous authority |

Each level has finite request, output, time, and retry limits.  Probe payloads
never include credentials, protected source, private configuration, or Human
Authority.  A provider API/request-shape/configuration failure is recorded
separately from a model-capability failure.  If an adapter-side mismatch is
demonstrated, it receives a focused regression and repair; if the external
provider cannot support the needed contract, the evidence identifies the
binding and exact missing capability rather than pretending the model is
qualified.

## G6O1 practical-use freeze

G6O1 remains `BLOCKED` with `actionable=false` in `GATE_STATUS.json`; it is
not VERIFIED and the Gate checker must not be induced to treat deferred work
as verified.  `spec/v2/G6O1_DEFERRED.md` becomes the companion record for the
Human decision that real paid-provider qualification, worst-case billing
evidence, deployment-owned paid budget, and G6O1-SIM runtime E2E are frozen
and non-blocking for the current Phase 7+ development roadmap.

That companion must preserve the original canonical requirements, reason,
resume triggers, and a step-by-step resume procedure.  Current State,
execution plan, and traceability distinguish evidence status (`NOT VERIFIED`)
from active-roadmap blocking (`false`).

## Error handling and metrics

Only meaningful results wake Codex.  Raw Worker conversation, stdout, stderr,
and patch bytes remain artifacts and are not copied into Supervisor metadata.
The ReviewPacket remains reference-first.  Existing durable ReviewDecision
records the reviewer role, findings, evidence references, required correction,
and decision time.

Metrics distinguish a review request from an actual review decision.  Probe
metrics are separate from routing evidence and must not hard-wire
EvidenceBasedRoutingPolicy while its sample/freshness/rollback conditions are
unmet.

## Tests and verification

Tests are written first for the intervention predicate, integration/rework
return paths, Codex-owned ready work, and wait-budget expiration.  Existing
orphan recovery tests remain unchanged.  Prompt changes receive focused
format-contract tests; probe code receives bounded request/result and failure
classification tests without live credentials.

Each implementation slice runs its focused suite.  Before commit/push, run
the full `tests/v2` suite, architecture check, compileall, and exact-head CI.
Real Worker Dogfood evidence is recorded separately and never substituted by
mocks or test success.
