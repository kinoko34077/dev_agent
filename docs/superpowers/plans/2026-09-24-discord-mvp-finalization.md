# Discord Human UI MVP Finalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the remaining Discord Human UI consistency gaps, keep the existing Core as the authority, and leave live-client gaps truthfully recorded before returning to the R9/Phase 8 roadmap.

**Architecture:** The Discord package remains a thin Gateway/UI adapter. History is synchronized into the existing Conversation Log with a durable per-channel/thread cursor; HumanRequest and Approval components are reconstructed from existing Core records and delivery metadata; outbound/archive repair uses the existing SQLite and projection boundaries. Semantic intent remains proposal-only and falls back to deterministic routing.

**Tech Stack:** Python 3.10+, SQLiteStateStore, existing Operation/Process Coordination/RuntimeCoordinator, optional discord.py 2.x, pytest.

**Spec:** User-provided “dev_agent Discord Human UI MVP finalization” instruction dated 2026-09-24.

## Global Constraints

- Do not add a Discord Scheduler, Task queue, Conversation DB, Approval DB, Agent framework, or UNKNOWN replay path.
- Core Task, HumanInteractionPort, Approval Authority, Conversation Log, DurableQueue, and RuntimeCoordinator remain the sources of truth.
- Discord history is bounded context only and never becomes ingress, Task, NOTE, HumanResponse, or Approval replay.
- Do not store or log `.env`, tokens, API keys, raw credentials, or unbounded Discord content.
- Formal Phase 8 and D9 Production Deployment gates remain unchanged.
- Skip broad regression runs when unchanged; run focused tests for changed behavior and rerun broader checks only when a shared Core contract changes or a focused failure requires it.

## Review Focus

- A 101+ message backlog must not skip the unpaged tail; test cursor advancement after full pages.
- A restarted bot must restore only unresolved HumanRequest/Approval views; test one-shot records are not restored.
- A Discord Thread must authorize against its parent channel for commands and components; test every interaction path.
- UI shortening must never change the full Core decision value; test long and 26–32 choice sets.
- A successful Discord send with a failed log append must reconcile the log without resending; test delivery/idempotency separately.

### Task 1: Correct bounded history pagination

**Files:**
- Modify: `src/dev_agent/discord/context.py`
- Modify if needed: `src/dev_agent/discord/binding.py`, `src/dev_agent/state/core_repository.py`, `src/dev_agent/state/sqlite_store.py`
- Test: `tests/v2/test_discord_history_context.py`

**Interfaces:** `sync_discord_history()` continues to use the existing history cursor contract and advances only to the last fetched page item unless the API is caught up.

- [x] Add regression coverage for full-page backlog continuation, duplicate IDs, and current-message-already-logged behavior.
- [x] Implement bounded page cursor advancement without using `current_message_id` as an unconditional maximum; preserve seed cutoff, idempotent log insertion, and no ingress replay.
- [x] Run the focused history tests again and confirm all paging cases pass.

### Task 2: Persistent components and parent-channel authorization

**Files:**
- Modify: `src/dev_agent/discord/bot.py`
- Modify: `src/dev_agent/discord/approval.py`, `src/dev_agent/discord/human.py` if callback boundaries require it
- Modify: `src/dev_agent/discord/renderer.py`
- Test: `tests/v2/test_discord_bot.py`, `tests/v2/test_discord_delivery.py`

**Interfaces:** `build_human_request_view()` and `build_approval_view()` retain their existing Core callbacks while emitting stable bounded `custom_id` values; startup restoration consumes existing pending records and delivery metadata only.

- [x] Add regression coverage for stable custom IDs, choice-shape policy, complete decision preservation, and persistent view boundaries.
- [x] Apply shared parent-channel binding authorization to commands and component callbacks.
- [x] Implement bounded custom-ID derivation, option-shape policy, and startup `add_view()` restoration without a new authority store.
- [x] Run the focused bot/delivery tests and confirm the persistent component contract.

### Task 3: Delivery reconciliation, archive safety, and long final responses

**Files:**
- Modify: `src/dev_agent/discord/outbound.py`
- Modify: `src/dev_agent/discord/conversation_archive.py`
- Modify: `src/dev_agent/discord/renderer.py`
- Modify if needed: existing StateStore delivery APIs
- Test: `tests/v2/test_discord_outbound.py`, `tests/v2/test_discord_conversation_archive.py`

**Interfaces:** Existing delivery keys remain idempotent; final responses expose bounded chunks with indexed keys; archive maintenance exposes attempt/success/error state and deletes active rows only after read-back verification.

- [x] Add regression coverage for send-success/log-failure reconciliation, archive backoff/error visibility, gzip/JSONL/count/hash read-back, and indexed long-response chunks.
- [x] Implement the smallest existing-store lookup/reconciliation path, archive verification/backoff, and paragraph-aware final chunking.
- [x] Run focused outbound/archive tests and inspect that failures are isolated without duplicate sends.

### Task 4: Standard-runner semantic proposal and read-only CHAT

**Files:**
- Modify: `src/dev_agent/discord/intent.py`
- Modify: `src/dev_agent/discord/composition.py`
- Modify: `src/dev_agent/discord/bot.py`
- Test: `tests/v2/test_discord_intent.py`, `tests/v2/test_discord_composition.py`, `tests/v2/test_discord_bot.py`

**Interfaces:** The resolver is an injected proposal-only callable using existing model/admission boundaries; deterministic authority commands bypass it, and resolver failure falls back to `resolve_plain_text()`.

- [x] Add regression coverage for strict proposal validation, deterministic fallback, zero-Task CHAT, terminal FOLLOW_UP context, and command bypass.
- [x] Implement the minimal adapter around existing Provider/ModelRequest admission; do not create a Discord provider registry. Keep CHAT read-only and improve deterministic fallback priority to FINAL → decision → progress → ACK.
- [x] Run focused intent/composition/bot tests.

### Task 5: Evidence, docs, verification, and live-boundary report

**Files:**
- Modify once: `docs/CURRENT_STATE.md`
- Modify once: `spec/v2/evidence/discord-conversation-runtime-next-20260924.json` or a new bounded evidence record
- Create if needed: `docs/superpowers/plans/2026-09-24-discord-mvp-finalization.md` (this plan)

- [x] Run changed Discord tests, architecture, compileall, credential scan, and full `tests/v2` because the existing StateStore schema boundary changed.
- [x] Commit and push implementation; verify exact-head CI.
- [x] Record only observed Gateway/client evidence; keep unavailable or unobserved round trips as `NOT_VERIFIED`.
- [x] Synchronize the bounded Evidence and Current State once for this implementation checkpoint; formal gates remain unchanged.
- [ ] Resume roadmap preflight with read-only model evidence refresh before any fresh R9 dispatch.
