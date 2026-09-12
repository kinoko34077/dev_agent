# Codex-Supervised DevFarm Dogfood Design

## Status and decision

This document defines the first development-only Supervisor boundary for
`v2/bootstrap`. It is an approved extension of the existing Commander / DevFarm
workflow, not a production scheduler, a new Task state machine, or an Agent
framework.

The current DevFarm proposal API is synchronous: `DevFarmOrchestrator.propose()`
does not expose a durable remote-job completion event. ChatGPT Desktop Scheduled
tasks can resume a chat on a time cadence, but the official product does not
provide a custom local DevFarm-artifact event that directly resumes Codex.
Therefore v0 uses a durable wake projection plus one changeable heartbeat
cadence. It must not claim immediate event-driven Codex wakeup.

## Goals

- Keep Codex as Supervisor, reviewer, integrator, and exception handler; use
  qualified Free Workers for narrow implementation work whenever safe.
- Preserve Commander Plans, manifests, attempt artifacts, Host Verification,
  protected-path policy, bounded attempts, and Git-proven integration as their
  existing authorities.
- Let an external Codex/ChatGPT heartbeat resume from durable, compact state
  instead of retaining a long model context or repeatedly reading worker output.
- Support reference-first handoffs, including validated external text
  references, without fetching or trusting an external payload as Control.
- Make a future direct event bridge replace the heartbeat without changing the
  Supervisor's public state or review boundary.

## Non-goals

- No OpenAI API, Claude API, Compression Service, MCP server, Discord bot,
  production daemon, automatic merge, automatic push, or automatic promotion.
- No automatic integration merely because Host Verification passes.
- No OS-sandbox claim beyond the existing Host Verification trust level.
- No external payload fetcher, uploader, or provider-specific temporary-text
  service dependency in this slice.
- No detached Worker process manager. Existing synchronous DevFarm dispatch
  remains its own bounded call.

## Components and authority

```text
Human specification authority
        |
Codex Supervisor / scheduled heartbeat
        |
CodexSupervisedCommanderRun (development-only composition)
        |                         |
CommanderPlanStore           HandoffEnvelope
        |                         |
DevFarm / Host Verification   external-text reference metadata
        |
Git-proven explicit integration
```

### Existing authorities retained

| Concern | Existing authority | Supervisor role |
| --- | --- | --- |
| Plan/task status and dependencies | `CommanderPlanStore` | Reads and composes; never duplicates task transitions. |
| Worker proposal and retry bounds | DevFarm manifest / Commander | Calls existing dispatch, collect, verify, and reassign functions. |
| Host verification and trust level | `DevFarmOrchestrator` / worker verifier | Requests existing verification; does not weaken `STATIC_ONLY` default. |
| Integration proof | `mark_integrated()` | Records Codex review and delegates only after target revision evidence exists. |
| Protected paths / secret outbound policy | Existing DevFarm policy | Does not create an override. |
| Human decisions | Human | Emits a durable `HUMAN_DECISION_REQUIRED` wake; never selects protected, budget, privacy, paid-provider, or roadmap policy changes. |

## Supervisor Run projection

`CodexSupervisedCommanderRun` will be a development-only facade around an
existing Commander `run_id`. A small durable sidecar under `.devfarm/` may store
only information that is not already represented by the Plan:

- `run_id` and `plan_id`;
- roadmap reference and current roadmap position;
- compact review, integration, and next-action policy;
- current heartbeat cadence and optional overall deadline;
- deduplicated meaningful wake records;
- proxy metrics: Codex wakes/reviews, Worker dispatches/successes/retries,
  inline/reference payload sizes, and artifact fetch count.

The sidecar is a resumable projection, not a second task scheduler or a second
task state machine. Task eligibility, attempts, dependency release, and
terminal status continue to be derived from the Commander Plan.

### Supervisor projection states

The sidecar may expose the following compact operational status values:

- `ACTIVE`: the run can make a bounded composition step now.
- `WAITING_FOR_WORKER`: worker tasks are still in an existing nonterminal
  Commander status; no Codex judgement is currently needed.
- `REVIEW_REQUIRED`: one or more `HOST_VERIFIED` task results require Codex
  review before integration.
- `HUMAN_DECISION_REQUIRED`: a policy/authority/roadmap decision is required.
- `BLOCKED`: an attempt limit, unknown outcome, verification boundary, or no
  eligible Worker prevents safe continuation.
- `COMPLETED`: the plan's declared bounded objective is terminal.

These values are derived from the Plan and wake records. They do not introduce
new production lifecycle transitions.

## Wake contract and cadence

### Meaningful wake records

Only the following are eligible to wake Codex:

- `HOST_VERIFIED_RESULT_READY`
- `WORKER_REJECTED_AFTER_RETRY`
- `INTEGRATION_CONFLICT`
- `ROADMAP_DECISION_REQUIRED`
- `NO_ELIGIBLE_WORKER`
- `HUMAN_DECISION_REQUIRED`
- `MILESTONE_REACHED`

Worker start, heartbeat, partial output, and unchanged status never create a
Codex wake record. Each record contains only task/attempt identity, Provider
and model identity, status, changed paths, patch digest, concise result and
verification summaries, known issues, and artifact references. It must not
copy a Worker conversation or raw artifact body into the record.

### One heartbeat, selectable cadence

ChatGPT Desktop Scheduled tasks are managed outside this repository. The
Supervisor will expose one current cadence selected from exactly `1`, `5`,
`10`, or `15` minutes. The desktop automation is a single current-thread
heartbeat whose recurrence is updated to the selected cadence; the design does
not create four concurrent timers.

Cadence is selected from the Worker deadline or expected duration:

| Expected remaining duration | Cadence |
| --- | --- |
| at most 5 minutes | 1 minute |
| more than 5 through 20 minutes | 5 minutes |
| more than 20 through 60 minutes | 10 minutes |
| more than 60 minutes or unspecified | 15 minutes |

A heartbeat does one compact `supervisor status`/`resume` action. If no
meaningful wake record is present it exits without Worker-log retrieval or
human-facing status text. It still consumes product usage according to the
account's Codex/ChatGPT limits; it is not an unlimited free execution channel.

When a future direct completion-event transport exists, it can call the same
`resume`/wake-record interface immediately and the cadence becomes inactive.

## Bounded composition

One `advance()` call must make at most one bounded pass:

1. Load and refresh the Commander Plan.
2. If worker tasks are `READY`, use the existing `dispatch_plan()` path.
3. Collect durable result artifacts through `collect_plan()`.
4. Verify only `PROPOSED` work through `verify_plan()` and the existing trust
   boundary.
5. Emit a compact `REVIEW_REQUIRED` wake for `HOST_VERIFIED` work.
6. Stop; do not auto-integrate or begin an unbounded loop.

Codex review is a separate explicit operation. It may approve an existing,
Git-proven integration through `mark_integrated()`, reject it, or make a
bounded reassign/rework decision. A rework handoff references the original
task, attempt, failure evidence, and review findings; it does not repeat the
entire original prompt or silently relax scope.

The initial implementation does not detach the synchronous proposal call. If a
provider request is in progress, the DevFarm process waits; Codex reasoning is
not repeatedly invoked during that tool/process wait. The next heartbeat sees
only the durable terminal artifact after that call returns.

## External text reference

`ExternalTextReference` is a typed Payload reference, not a transport client.
It contains:

- `type: "external_text"`;
- an absolute HTTPS `location` without embedded credentials;
- SHA-256 of the referenced UTF-8 text and declared byte size;
- `created_at` and optional `expires_at`;
- an optional source label with no service-specific semantics.

It is valid only in `payload_mode="reference"`. The Handoff protocol validates
the metadata but does not download the location. A failed/unavailable fetch is
an explicit unresolved payload condition, never a cue to infer its contents.
External text remains untrusted Payload: it cannot change Control, authority,
conditions, cautions, exclusions, approvals, budget, or protected-path scope.
The creator of such a reference must already comply with the existing secret
and outbound policy; this type never grants an outbound exception.

## Test strategy

Tests precede production changes and cover:

- durable supervisor projection round-trip and refresh without duplicated
  Planner/Worker task state;
- readiness, proposed-result verification, review-required event deduplication,
  blocked/human-decision outcomes, and bounded `advance()` behavior;
- cadence selection at each boundary and no four-timer representation;
- review/rework Handoff reference-first output and no raw result duplication;
- integration helper delegation to existing Git-evidence validation;
- ExternalTextReference validation, serialization, HTTPS/credential rejection,
  expiry ordering, and Control/Payload isolation;
- one-cycle Handoff and existing DevFarm/Commander regressions.

An actual external-Worker integration Dogfood remains subject to the existing
Host Verification trust rules. Under `STATIC_ONLY`, an external patch cannot
be accepted or integrated. A real accepted external Worker attempt needs the
existing explicit attempt-scoped `TRUSTED_HOST_EXEC` approval or a future
`OS_SANDBOXED` implementation; this design does not bypass that gate.

## Operational use after implementation

1. Codex creates a bounded Commander Plan and a Supervisor Run with a roadmap
   reference and cadence policy.
2. A current-thread Scheduled heartbeat is created or updated outside the repo
   to the Run's current cadence only while the Run is active.
3. The heartbeat resumes the Supervisor from durable summaries. It remains
   quiet unless a meaningful wake exists.
4. Codex reviews only Host-verified evidence, then explicitly integrates or
   reassigns through existing boundaries.
5. On a human decision, terminal safety block, milestone, or bounded Plan
   completion, the heartbeat is paused and a human-facing result is produced.
