# Discord Conversation Runtime Design

**Date:** 2026-09-24  
**Scope:** `v2/bootstrap`  
**Status:** Approved for implementation

## Goal

Extend the existing Discord Human UI so it can perform durable user-requested
waiting, preserve bounded conversation context, distinguish chat from work
intent, and publish final responses without creating a second scheduler,
conversation database, or task runtime.

## Existing boundaries to preserve

- `DurableQueue` remains the only wake/scheduling authority.
- `SQLiteStateStore` remains the only durable state owner.
- `RuntimeCoordinator` remains the only task execution owner.
- `DiscordHumanFacingSender` remains the single Discord send boundary.
- `HumanRequest`, Approval Authority, Host Verification, and reconciliation
  semantics remain unchanged.
- Discord history is context only; it never replays a task, mailbox message,
  approval, or human response.
- Phase 8 and D9 Gate values remain unchanged.

## Architecture

### 1. Durable user delay

User delays are represented as an existing `WAITING_DEPENDENCY` task with
`metadata.wait_reason = "user_delay"` and a bounded `metadata.wait_until_epoch`.
The task is deferred through the existing queue `defer_until()` path and woken
by the existing maintenance path. No Discord timer or new scheduler is added.

The first implementation accepts deterministic bounded durations from a
plain-text request. The maximum delay is one hour. A wait continuation records
the requested duration and, when the task resumes, emits a final response
through the normal Discord outbound projection.

### 2. Conversation Log

A `discord_conversation_messages` table is added to the existing StateStore.
It is a message log, not a task state machine. Rows are idempotent by
`message_id` and contain only bounded, sanitized message content plus channel,
thread, speaker, direction, reply relation, message kind, root, run, and source
metadata.

Inbound rows are written after authorized ingress acceptance. Outbound rows are
written only after Discord confirms a send and returns a message ID. The sender
does not own persistence; the outbound publisher/recorder boundary does.

### 3. History synchronization and context

Discord API history is used only to reconcile missing conversation-log rows.
It never enters the operation ingress path. Context is read from the local log,
bounded to 50 messages and 16,000 characters, with the current message
excluded and reply-chain messages preferred. Existing sanitizer and
authorization filters are reused.

### 4. Intent resolution

Deterministic paths keep precedence: HumanRequest replies, Approval actions,
explicit `/cancel`, `/interrupt`, `/parallel`, `/new`, and status queries.
Only ordinary plain text reaches the resolver. The resolver produces a bounded
proposal among `CHAT`, `NEW_REQUEST`, `FOLLOW_UP`, `READ_QUERY`, and `WAIT`.
Host validation accepts only safe routing fields. `CHAT` never creates a Task;
`WAIT` enters the durable delay path; `FOLLOW_UP` references an existing
conversation binding or terminal root. Resolver failure falls back to the
existing deterministic routing behavior.

No resolver output can establish approval, cancellation, interruption, or a
destructive operation.

### 5. Final response projection

Final natural-language responses are separate from progress markers. They use
the existing sender and delivery idempotency boundary, then append an outbound
conversation-log row containing the returned Discord message ID and relation
metadata.

### 6. Archive

Archival is a later slice after the log and live context path are stable. Rows
older than 30 days or beyond 2,000 messages per channel are exported as
JSONL.gz with flush/fsync, count/hash manifest, and only then removed from the
active table. Any failure leaves active rows intact. Archive reads are
read-only and bounded.

## Implementation slices

1. Durable wait contract and fake-clock tests.
2. Conversation-log schema, repository, inbound/outbound idempotency.
3. Local context and API history reconciliation without replay.
4. Deterministic intent proposal/validation with safe fallback.
5. Final response projection and live wait/follow-up smoke.
6. Archive export/search and documentation/evidence synchronization.

Each slice has focused tests before the next slice. Discord-only verification is
used for the initial implementation; unrelated full regression remains a
separate release check.

## Safety and failure behavior

- Unknown or ambiguous external effects never enter the wait/repair path.
- Invalid or unauthorized Discord input is not logged as accepted ingress.
- Secret-bearing content is sanitized before persistence or model context.
- Duplicate Discord delivery or history sync is a no-op.
- Archive failure is fail-safe: active rows remain.
- All live claims remain `NOT_VERIFIED` until observed through a real Discord
  client.
