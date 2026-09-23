# Discord Human Runtime Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the remaining Discord Human UI runtime gaps without adding a second scheduler, task store, or conversation authority.

**Architecture:** Keep RuntimeCoordinator, OperationService, SQLiteStateStore, existing conversation log, and bounded Discord outbound projection as the sources of truth. Fix the final-response event contract, preserve reply metadata at ingress and outbound delivery, connect conversation archival to the existing bounded maintenance path, and make active-run wait semantics explicit through existing durable Operation boundaries.

**Tech Stack:** Python, SQLiteStateStore, pytest, discord.py adapter contracts, existing Operation/Queue maintenance.

**Spec:** User-provided attachment `e54bb0b6-33db-40c1-a5a4-b536312a962e/貼り付けたテキスト.txt`.

## Global Constraints

- Do not add a Discord scheduler, second Task queue, conversation database, or approval authority.
- Do not persist tokens, raw provider responses, raw Discord payloads, or secrets.
- UNKNOWN/reconciliation semantics and Host/Core authority remain unchanged.
- Live Discord verification is evidence-only and cannot be claimed from local tests.
- Active wait must use existing durable Operation/Queue boundaries and must not sleep inside a Worker or Discord event loop.

## Review Focus

- A real `task.completed` payload uses `text`, not only `text_segments`; test the production event shape.
- A Discord reply relation must survive both ingress and history sync; test numeric message-reference extraction and filtering.
- Active-run wait must not silently become NOTE when the user explicitly requests a delayed response; test the existing binding remains safe.
- Archive maintenance must be idempotent and failure-safe; test empty and write-failure passes.
- No live-client claim may be promoted by synthetic tests; update Current State with explicit unverified fields.

### Task 1: Final response projection contract

**Files:**
- Modify: `src/dev_agent/discord/outbound.py`
- Test: `tests/v2/test_discord_outbound.py`

**Interfaces:**
- Consumes: existing `task.completed` event payloads from SQLiteStateStore.
- Produces: one bounded final Discord projection for either `payload["text"]` or the legacy `payload["text_segments"]`, with existing delivery idempotency.

- [ ] **Step 1: Write the failing test**

Add a test using `payload={"text": ["...", "..."]}` and assert `publish_once()["final_responses"] == 1` and the rendered text is sent once.

- [ ] **Step 2: Run the focused test and observe the expected failure**

Run: `python -m pytest tests/v2/test_discord_outbound.py -k final_response -q`
Expected: the new production-event-shape test fails because the publisher currently reads only `text_segments`.

- [ ] **Step 3: Implement the minimal compatibility projection**

Read `payload["text"]` first and fall back to `payload["text_segments"]`; keep existing bounded rendering and delivery keys unchanged.

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `python -m pytest tests/v2/test_discord_outbound.py -k final_response -q`
Expected: all final-response tests pass.

- [ ] **Step 5: Commit the task**

Commit message: `fix: project task completion text to discord`

### Task 2: Conversation reply metadata

**Files:**
- Modify: `src/dev_agent/discord/context.py`
- Modify: `src/dev_agent/discord/bot.py`
- Modify: `src/dev_agent/discord/outbound.py`
- Test: `tests/v2/test_discord_history_context.py`
- Test: `tests/v2/test_discord_outbound.py`

**Interfaces:**
- Consumes: discord.py message/reference metadata and existing ConversationMessage.
- Produces: sanitized `reply_to_message_id` on inbound, history-sync, and outbound acknowledgment/projection rows.

- [ ] **Step 1: Add failing metadata tests**

Cover an inbound message with `message.reference.message_id`, a history item with the same metadata, and an outbound send result whose channel message references the triggering message when available.

- [ ] **Step 2: Run the focused tests and observe the expected failure**

Run: `python -m pytest tests/v2/test_discord_history_context.py tests/v2/test_discord_outbound.py -k reply -q`
Expected: rows currently contain `reply_to_message_id is None`.

- [ ] **Step 3: Implement bounded reference extraction**

Add one helper that accepts only a decimal Discord message ID, use it for ingress/history conversion, and pass an optional source reply ID through the existing outbound callback metadata without changing the send contract for callers that do not provide it.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run: `python -m pytest tests/v2/test_discord_history_context.py tests/v2/test_discord_outbound.py -k reply -q`
Expected: reference IDs are preserved and malformed/non-numeric references are omitted.

- [ ] **Step 5: Commit the task**

Commit message: `feat: preserve discord reply context in conversation log`

### Task 3: Durable active wait and archive maintenance boundaries

**Files:**
- Modify: `src/dev_agent/discord/composition.py`
- Modify: `src/dev_agent/operation.py`
- Modify: `src/dev_agent/discord/conversation_archive.py`
- Modify: `src/dev_agent/runtime/` only if the existing maintenance hook requires it
- Test: `tests/v2/test_discord_wait.py`
- Test: `tests/v2/test_discord_conversation_archive.py`
- Test: `tests/v2/test_operation.py`

**Interfaces:**
- Consumes: existing `submit_delayed`, `DurableQueue.defer_queued_until`, and maintenance tick.
- Produces: explicit active-run wait behavior that remains durable and a bounded archive maintenance call that is safe when there is nothing eligible.

- [ ] **Step 1: Add failing active-run wait and archive maintenance tests**

Assert an explicit wait proposal does not become an untyped NOTE when the binding is active; it either creates a durable wait continuation through the Core boundary or returns a bounded non-mutating refusal. Also assert the maintenance hook invokes archive once per bounded pass and preserves rows on archive failure.

- [ ] **Step 2: Run the focused tests and observe the expected failure**

Run: `python -m pytest tests/v2/test_discord_wait.py tests/v2/test_discord_conversation_archive.py tests/v2/test_operation.py -k "wait or archive" -q`
Expected: active wait currently routes to NOTE and runtime archive maintenance is not connected.

- [ ] **Step 3: Implement the smallest Core-owned behavior**

Use an existing Task/Operation continuation or explicit bounded response-delay artifact; never sleep in Discord or Worker code, never create a Discord timer, and keep unrelated Tasks runnable. Invoke archive only from the existing maintenance path with bounded age/count settings and bounded error projection.

- [ ] **Step 4: Run the focused tests and verify they pass**

Run: `python -m pytest tests/v2/test_discord_wait.py tests/v2/test_discord_conversation_archive.py tests/v2/test_operation.py -k "wait or archive" -q`
Expected: durable wait and archive maintenance tests pass without introducing a second loop.

- [ ] **Step 5: Commit the task**

Commit message: `feat: connect discord wait and archive maintenance`

### Task 4: Evidence and Current State synchronization

**Files:**
- Modify: `docs/CURRENT_STATE.md`
- Create or modify: `spec/v2/evidence/discord-conversation-runtime-20260924.json`
- Modify: `docs/V2_EXECUTION_PLAN.md` only where the current snapshot is stale

**Interfaces:**
- Consumes: fresh focused-test, architecture, compile, and CI outputs.
- Produces: truthful documentation distinguishing local verification from real Discord-client verification.

- [ ] **Step 1: Update the evidence projection**

Record the final-response contract fix, reply metadata, durable wait behavior, archive hook status, and explicitly keep live Discord wall-clock/client round trips as `NOT_VERIFIED` unless a real client observation exists.

- [ ] **Step 2: Run read-only repository checks**

Run: `python scripts/check_architecture.py` and `python -m compileall -q src recovery scripts`.
Expected: `ARCHITECTURE_PASS` and exit code 0.

- [ ] **Step 3: Run focused and full regression**

Run: `python -m pytest tests/v2 -q`.
Expected: zero failures; any pre-existing environment skip is reported without being reclassified.

- [ ] **Step 4: Commit documentation and push the verified slice**

Run credential scan, then commit with `docs: sync discord conversation runtime hardening`, push to `origin/v2/bootstrap`, and report the resulting HEAD. Do not include `.env` or any token in the commit.

## Completion Checklist

- [ ] P0 final response production payload shape is covered and projected.
- [ ] Reply metadata is preserved without raw payload persistence.
- [ ] Active wait behavior is explicit and durable or safely refused; it is not silently NOTE.
- [ ] Archive is connected only through existing maintenance and remains failure-safe.
- [ ] Focused tests, full `tests/v2`, architecture, compile, credential scan, commit, push, and exact-head CI are verified.
- [ ] Live Discord client claims remain truthful and separate from local evidence.
