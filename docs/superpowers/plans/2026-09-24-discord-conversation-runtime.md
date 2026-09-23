# Discord Conversation Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add durable user waits, bounded persistent Discord conversation context, safe plain-text intent routing, final response projection, and archival using the existing dev_agent runtime boundaries.

**Architecture:** Reuse `DurableQueue.defer_until()` and RuntimeCoordinator maintenance for waits; add a message-only log to the existing SQLite StateStore; route only sanitized bounded context into existing Operation/Coordination inputs; keep explicit commands and authority decisions deterministic. No Discord scheduler, task database, or conversation manager is introduced.

**Tech Stack:** Python, SQLite, existing `DurableQueue`, `SQLiteStateStore`, `discord.py`, pytest.

**Spec:** `docs/superpowers/specs/2026-09-24-discord-conversation-runtime-design.md`

## Global Constraints

- Do not add a second scheduler, task queue, StateStore, runtime, Redis service, or Discord conversation SSOT.
- Preserve HumanRequest, Approval, Host Verification, reconciliation, and Gate authority.
- History is context only and must never replay ingress, mailbox, approval, or HumanResponse records.
- Persist only bounded sanitized content; never persist tokens, credentials, raw exceptions, or unbounded Discord history.
- Maximum user delay is 3600 seconds.
- Discord-only focused tests are required for each slice; unrelated full regression is not required for every slice.

## Review Focus

- A restart during a user wait must wake at most once and preserve the task’s wait reason.
- A duplicated inbound or outbound Discord message must not create a second log row or task.
- History sync must not replay old messages into Operation or Coordination.
- Resolver failure or malformed proposal must fall back safely and never create approval/cancel/interrupt authority.
- Archive failure must leave active rows available and searchable.

### Task 1: Durable User Wait Contract

**Files:**
- Create: `src/dev_agent/discord/wait.py`
- Modify: `src/dev_agent/operation.py` only where an existing public wait/defer boundary is required
- Test: `tests/v2/test_discord_wait.py`

**Interfaces:**
- `parse_user_delay(text: str, *, max_seconds: int = 3600) -> int | None` returns seconds only for an explicit bounded wait request.
- `UserDelayRequest(seconds: int, source: str)` is immutable and sanitized.
- `defer_user_delay(task, request, queue, now_epoch: float) -> float` uses `queue.defer_until()` and writes only existing task metadata (`wait_reason`, `wait_until_epoch`).

- [ ] Write tests for `20秒待ってから返事して`, `10 seconds`, malformed/negative/over-one-hour input, and deterministic wake metadata.
- [ ] Run `pytest tests/v2/test_discord_wait.py -q`; verify the new tests fail because the parser/defer helper is absent.
- [ ] Implement the bounded parser and queue adapter without introducing a timer loop.
- [ ] Run the focused test file and confirm green output.
- [ ] Add a restart/idempotent wake test using the existing queue snapshot and `wake_due()`.
- [ ] Run the focused wait tests again.

### Task 2: Persistent Conversation Log

**Files:**
- Modify: `src/dev_agent/state/schema.py`
- Modify: `src/dev_agent/state/core_repository.py`
- Modify: `src/dev_agent/state/sqlite_store.py`
- Create: `src/dev_agent/discord/conversation_log.py`
- Test: `tests/v2/test_discord_conversation_log.py`

**Interfaces:**
- `ConversationMessage` contains `message_id`, `guild_id`, `channel_id`, `thread_id`, `created_at`, `received_at`, `speaker_role`, `speaker_id`, `speaker_name`, `direction`, `content`, `reply_to_message_id`, `message_kind`, `root_id`, `run_id`, and `source`.
- `ConversationLog.append(message) -> bool` is idempotent by `message_id`.
- `ConversationLog.list_context(binding_key, *, limit=50, max_chars=16000, reply_to_message_id=None) -> tuple[ConversationMessage, ...]` returns bounded local context.
- `ConversationLog.mark_outbound(...)` is called only after a send returns a Discord message ID.

- [ ] Write schema migration and repository tests for inbound deduplication, outbound recording, fields, and secret sanitization.
- [ ] Run the focused tests to observe the expected missing-table/API failures.
- [ ] Add the next StateSchema migration and repository facade methods; keep schema versioning ordered.
- [ ] Implement the log boundary and bounded query ordering.
- [ ] Run focused tests and verify green.

### Task 3: History Reconciliation and Context Handoff

**Files:**
- Modify: `src/dev_agent/discord/history.py`
- Create: `src/dev_agent/discord/context.py`
- Modify: `src/dev_agent/discord/composition.py`
- Modify: `src/dev_agent/discord/bot.py`
- Test: `tests/v2/test_discord_history_context.py`

**Interfaces:**
- `build_bounded_context(log, binding, current_message_id, reply_to_message_id=None) -> dict[str, list[dict[str, str]]]` reads only local log rows.
- `sync_history_once(history_messages, log, binding, *, latest_message_id=None) -> int` records missing rows and never calls Operation/Coordination ingress.
- Existing `DiscordIngressEvent.history_context` receives bounded context projection for `NEW_REQUEST`, `PARALLEL`, and `NOTE`.

- [ ] Add tests for 20/50-message and 8k/16k-char bounds, same-channel filtering, current-message exclusion, authorized Human/Bot filtering, and no replay.
- [ ] Run focused history/context tests and confirm failure before implementation.
- [ ] Implement local-log context and a reconciliation-only history sync path.
- [ ] Attach context to existing structured Operation inputs and immutable NOTE artifacts; do not concatenate raw history into NOTE text.
- [ ] Run focused Discord tests and verify green.

### Task 4: Safe Plain-Text Intent Proposal

**Files:**
- Create: `src/dev_agent/discord/intent.py`
- Modify: `src/dev_agent/discord/adapter.py`
- Modify: `src/dev_agent/discord/composition.py`
- Test: `tests/v2/test_discord_intent.py`

**Interfaces:**
- `IntentKind = CHAT | NEW_REQUEST | FOLLOW_UP | READ_QUERY | WAIT`.
- `IntentProposal(kind, objective, delay_seconds=None, binding_key=None, confidence=None)` is proposal-only.
- `resolve_plain_text(text, *, binding, task_state, deterministic_delay_parser) -> IntentProposal` never returns destructive authority.
- `validate_intent(proposal, *, binding, task_state) -> IntentProposal` rejects invalid or unsafe proposals.

- [ ] Write tests proving explicit command precedence, `CHAT` creates no task, active follow-up routing, wait routing, malformed proposal fallback, and no approval/cancel/interrupt authority.
- [ ] Run focused intent tests and confirm red.
- [ ] Implement deterministic proposal/validation and conservative fallback to current routing.
- [ ] Wire only plain text through the resolver; keep HumanRequest replies and explicit commands first.
- [ ] Run focused intent and existing Discord composition tests.

### Task 5: Final Response Projection and Durable Wait Smoke

**Files:**
- Modify: `src/dev_agent/discord/delivery.py`
- Modify: `src/dev_agent/discord/outbound.py`
- Modify: `src/dev_agent/discord/renderer.py`
- Modify: `src/dev_agent/discord/composition.py`
- Test: `tests/v2/test_discord_final_response.py`

**Interfaces:**
- `DiscordHumanFacingSender.send()` remains the only send operation.
- `DiscordOutboundPublisher.publish_final_responses()` emits a natural-language final projection once per task/event marker and records the outbound conversation message after send success.

- [ ] Write tests for final-response idempotency, outbound log recording only after a returned message ID, and wait completion rendering.
- [ ] Run focused final-response tests and confirm red.
- [ ] Implement the projection using existing binding/delivery metadata and sender boundary.
- [ ] Run focused tests and existing Discord outbound tests.
- [ ] Run the permitted live smoke: send a 20-second wait request, observe durable waiting, then observe one final response.

### Task 6: Archive and Read-Only Search

**Files:**
- Create: `src/dev_agent/discord/conversation_archive.py`
- Modify: `src/dev_agent/discord/conversation_log.py`
- Test: `tests/v2/test_discord_conversation_archive.py`

**Interfaces:**
- `archive_eligible_messages(log, archive_root, now, max_age_days=30, per_channel_limit=2000) -> ArchiveManifest` writes JSONL.gz plus manifest and removes rows only after fsync/hash/count verification.
- `search_archive(archive_root, binding_key, query, *, limit=50) -> tuple[ConversationMessage, ...]` is read-only and bounded.

- [ ] Write fixture tests for age/count eligibility, manifest hash/count, archive failure retention, and bounded search.
- [ ] Run focused archive tests and confirm red.
- [ ] Implement temp-file write, flush/fsync, atomic manifest completion, and post-verification deletion.
- [ ] Run focused archive tests and verify green.

### Task 7: Documentation, Evidence, and Release Verification

**Files:**
- Modify: `docs/CURRENT_STATE.md`
- Modify: relevant Discord operator/setup documentation
- Add: bounded Evidence record under the existing evidence location
- Test: existing Discord focused suite

- [ ] Update docs with implementation status and keep live claims unverified until observed.
- [ ] Run the Discord-focused test suite.
- [ ] Run compileall and the relevant architecture/read-only checks.
- [ ] Run credential/secret scan without sending `.env` or tokens.
- [ ] Review the diff, commit, push to `origin/v2/bootstrap`, and verify exact-head CI.

