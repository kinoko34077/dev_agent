# Discord Conversation Runtime Next Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the remaining Discord conversation-runtime gaps without adding a second scheduler, queue, conversation database, or authority layer.

**Architecture:** Extend the existing Core-owned Task metadata/checkpoint and DurableQueue boundaries for active user delays. Preserve the SQLite Conversation Log as the only durable conversation source, adding only bounded sync cursors and richer context projections. Keep semantic intent proposal-only with deterministic authority commands and deterministic fallback.

**Tech Stack:** Python, SQLiteStateStore, DurableQueue, RuntimeCoordinator maintenance, discord.py, pytest.

**Spec:** User-provided Discord conversation runtime instruction dated 2026-09-24.

## Global Constraints

- Do not create a Discord Scheduler, Task queue, Conversation DB, Redis, LangGraph, or independent authority layer.
- Do not replay Discord history into Task, NOTE, HumanResponse, or Approval ingress.
- Active WAIT must not resume before `accepted_at + delay_seconds`.
- HumanRequest/Approval precedence and explicit command authority remain deterministic and higher priority than proposal-only intent.
- Do not store tokens, credentials, raw unbounded Discord payloads, or raw conversation dumps in tracked files or Evidence.
- Formal Phase 8 and D9 gates remain unchanged.

## Review Focus

- A running Task receiving WAIT must park only at a cooperative checkpoint and release its Worker lease.
- A WAIT request must never produce a success message before it is durably scheduled; a deferred checkpoint must be visibly distinct.
- Reply targets outside the recent window must be boundedly included without loading all history.
- Initial/incremental history sync must be context-only and idempotent.
- CHAT/FOLLOW_UP must not silently create mutation authority or bypass explicit commands.

### Task 1: Active durable WAIT

**Files:**
- Modify: `src/dev_agent/state/sqlite_store.py`
- Modify: `src/dev_agent/state/core_repository.py`
- Modify: `src/dev_agent/operation.py`
- Modify: `src/dev_agent/runtime/controller.py`
- Modify: `src/dev_agent/discord/composition.py`
- Modify: `src/dev_agent/discord/renderer.py`
- Modify: `src/dev_agent/discord/bot.py`
- Test: `tests/v2/test_discord_composition.py`, `tests/v2/test_operation_runtime.py`, `tests/v2/test_discord_bot.py`

**Interfaces:** `OperationService.request_user_delay(config, task_id, delay_seconds, source)` returns a bounded scheduling result; Controller consumes `task.metadata.user_delay_request` at a safe loop boundary and returns `WAITING_DEPENDENCY`; WorkerRunner’s existing `wait_until_epoch` finalization calls `DurableQueue.defer_until`.

- [x] Add a failing test proving an active leased Task stores an accepted wake time, is parked by Controller, and is not woken before that time.
- [x] Add a failing test proving WAIT UI distinguishes `WAIT_DEFERRED`, `WAIT_ACCEPTED`, and failure.
- [x] Implement atomic metadata merge for the user-delay request and the Controller checkpoint branch.
- [x] Route active Discord WAIT through the Core request boundary and keep queued WAIT on `submit_delayed`.
- [x] Run focused WAIT tests and the runtime regression.

### Task 2: Rich bounded conversation context and reply-chain selection

**Files:**
- Modify: `src/dev_agent/discord/history.py`
- Modify: `src/dev_agent/discord/context.py`
- Modify: `src/dev_agent/discord/adapter.py`
- Modify: `src/dev_agent/discord/composition.py`
- Test: `tests/v2/test_discord_history_context.py`, `tests/v2/test_discord_composition.py`

**Interfaces:** `DiscordHistoryMessage` carries bounded metadata; context projection preserves message identity, speaker metadata, reply reference, kind, root, and run where safe. A reply target and up to three parent references may be selected from the existing log.

- [x] Add failing metadata and old-reply tests.
- [x] Implement bounded metadata sanitization and reply-chain inclusion.
- [x] Keep current-message exclusion and authorization filtering.
- [x] Run focused context tests.

### Task 3: Durable initial and incremental history sync

**Files:**
- Modify: `src/dev_agent/state/schema.py`
- Modify: `src/dev_agent/state/core_repository.py`
- Modify: `src/dev_agent/state/sqlite_store.py`
- Modify: `src/dev_agent/discord/binding.py`
- Modify: `src/dev_agent/discord/context.py`
- Modify: `src/dev_agent/discord/bot.py`
- Test: `tests/v2/test_discord_history_context.py`, `tests/v2/test_discord_bot.py`, `tests/v2/test_state_schema.py`

**Interfaces:** StateStore keeps only per-binding sync cursor metadata. Initial seed is bounded by message count and age; subsequent sync uses the latest cursor and existing message-id idempotency. Sync never calls Core ingress.

- [x] Add failing cursor/seed/incremental tests.
- [x] Add schema migration and StateStore cursor methods.
- [x] Implement context-only bounded history sync with configurable limits.
- [x] Run migration and Discord-focused tests.

### Task 4: Archive throttling and outbound completion logging

**Files:**
- Modify: `src/dev_agent/discord/outbound.py`
- Modify: `src/dev_agent/discord/bot.py`
- Test: `tests/v2/test_discord_outbound.py`

- [x] Add failing throttle test.
- [x] Add monotonic last-maintenance guard inside the existing projection pass.
- [x] Preserve final-only behavior when a task completion has text.
- [x] Run focused outbound tests.

### Task 5: Proposal-only semantic intent, terminal FOLLOW_UP, and read-only CHAT

**Files:**
- Modify: `src/dev_agent/discord/intent.py`
- Modify: `src/dev_agent/discord/adapter.py`
- Modify: `src/dev_agent/discord/core.py`
- Modify: `src/dev_agent/discord/composition.py`
- Modify: `src/dev_agent/discord/renderer.py`
- Test: `tests/v2/test_discord_intent.py`, `tests/v2/test_discord_adapter.py`, `tests/v2/test_discord_core.py`

- [x] Add failing tests for terminal FOLLOW_UP metadata and CHAT zero-Task behavior.
- [x] Keep deterministic commands and WAIT parsing ahead of any resolver.
- [x] Add only a bounded injected proposal resolver; failed/unavailable resolver falls back deterministically.
- [x] Make CHAT read-only and bounded; never mutate or call tools.
- [x] Run focused intent/core tests.

### Task 6: Verification and documentation

**Files:**
- Modify: `docs/CURRENT_STATE.md`
- Modify: `spec/v2/evidence/discord-conversation-runtime-20260924.json`
- Modify: `docs/superpowers/plans/2026-09-24-discord-conversation-runtime-next.md`

- [x] Run focused Discord tests.
- [x] Run `tests/v2`, architecture, compileall, diff check, and credential scan.
- [x] Record bounded implementation evidence and the observed real Discord WAIT park/wake timestamps without storing message content or credentials; leave the remaining client scenarios unverified.
- [x] Commit and push to `origin/v2/bootstrap`, then verify exact-head CI (`950baaf`, core 3.10/3.11 and provider-smoke green).
