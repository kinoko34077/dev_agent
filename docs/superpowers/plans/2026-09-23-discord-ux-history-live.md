# Discord UX, bounded history, and live verification Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

Goal: Add a single serialized Discord human-facing send boundary with typing UX, improve bounded renderer output, and pass sanitized same-channel history context into new Core requests without creating a second task or conversation system.

Architecture: The existing Discord bot and DiscordOutboundPublisher remain the only UI projection owners. A small sender object owns only per-channel Discord send serialization and typing delay; Core still owns Tasks, mailboxes, approvals, HumanRequests, and execution. A bounded history collector reads only the current Discord channel/thread and attaches immutable context to the existing ingress event, which DiscordRuntimeComposition maps into structured Task inputs.

Tech Stack: Python 3.10+, discord.py, asyncio, existing SQLite StateStore, pytest, existing renderer and Core boundaries.

Spec: User-provided dev_agent Discord UX・履歴文脈・Live検証 追加実装指示書 in the current task.

## Global Constraints

- Do not add a Discord Scheduler, Conversation Manager, Redis, second StateStore, history replay engine, or Task lifecycle.
- Keep Discord history as bounded context only; never replay it as a Task, mailbox message, Approval, or HumanResponse.
- Read only the same channel/thread, authorized Human messages, and this dev_agent Bot messages.
- Bound history to 20 messages and 8,000 characters, redact secrets, and exclude the current message.
- Keep Host/Core authority, HumanRequest correlation, Approval authority, idempotency, and existing runtime ownership unchanged.
- Bot typing delay defaults to approximately 2 seconds in the standard runner; tests may inject zero delay.
- Do not commit .env, tokens, raw Discord conversation, or raw exception text.

## Review Focus

- A missing Message Content Intent must remain a bounded rejected ingress and must not create a Task or leak message content.
- A same-channel Bot send must remain ordered under concurrent progress/HumanRequest/ack sends.
- A history item from another channel, unauthorized Human, unrelated Bot, or the current message must never enter discord_context.
- History context must not be mistaken for a new ingress event or replayed after restart.
- Active WAITING bindings must keep ordinary text on the NOTE mailbox path while HumanRequest replies retain exact-response precedence.

### Task 1: Unified human-facing Discord sender and readable renderers

Files:
- Create: src/dev_agent/discord/delivery.py
- Modify: src/dev_agent/discord/bot.py
- Modify: src/dev_agent/discord/renderer.py
- Modify: src/dev_agent/discord/outbound.py only where the existing callback contract needs the sender boundary
- Test: tests/v2/test_discord_delivery.py
- Test: tests/v2/test_discord_bot.py
- Test: tests/v2/test_discord_outbound.py

Interfaces:
- Produces DiscordHumanFacingSender.send(channel, content, view=None) and build_bot(..., typing_delay_seconds=2.0).
- Consumes the existing DiscordOutboundPublisher send callback and all existing Bot acknowledgement/read/HumanResponse paths.

- [ ] Write failing tests for injected typing sleep, per-channel ordering, short acknowledgement text, readable status bullets, and HumanRequest output without an internal Request ID.
- [ ] Run the focused tests and confirm they fail because the sender and new renderer behavior do not yet exist.
- [ ] Implement the minimal sender with one asyncio.Lock per channel/thread, channel.typing(), injected sleep, and one send operation; do not add a queue or retry engine.
- [ ] Route normal acknowledgement, read projection, HumanResponse acknowledgement, outbound progress, HumanRequest, and Approval sends through that sender in the standard runner.
- [ ] Keep progress projection latest-only as it is today; do not add an in-memory backlog. Preserve non-droppable HumanRequest, Approval, and error projections.
- [ ] Replace full 受信: <content> echo with short accepted/intervention acknowledgements and format status/HumanRequest as bounded sections and bullets.
- [ ] Run the focused Discord tests and confirm they pass, then run the existing Discord suite.

### Task 2: Bounded same-channel history context

Files:
- Create: src/dev_agent/discord/history.py
- Modify: src/dev_agent/discord/adapter.py
- Modify: src/dev_agent/discord/bot.py
- Modify: src/dev_agent/discord/composition.py
- Test: tests/v2/test_discord_history.py
- Test: tests/v2/test_discord_adapter.py
- Test: tests/v2/test_discord_composition.py

Interfaces:
- Produces collect_discord_history(channel, current_message_id, authorizer, guild_id, channel_id, bot_user_id, limit=20, max_chars=8000) and an immutable bounded history message representation.
- Consumes DiscordIngressEvent and produces existing OperationService.submit(..., inputs=...) structured input under discord_context.

- [ ] Write failing tests for current-message exclusion, 20-message cap, 8,000-character cap, chronological ordering, same channel/thread scope, authorized Human filtering, dev_agent Bot filtering, unrelated Bot exclusion, secret redaction, and no Task replay.
- [ ] Run the focused history tests and confirm they fail because no collector/event context exists.
- [ ] Implement the collector using channel.history(..., before=current_message, oldest_first=True), bounded sanitization, role projection, and no persistence of the raw history.
- [ ] Extend the ingress event with optional immutable context without changing message idempotency or classification semantics.
- [ ] Gather history only after the current message is accepted; attach it to new Core requests as structured inputs["discord_context"]. Do not build Planner prompts in Discord.
- [ ] Keep NOTE, HumanRequest reply, Approval, and history backfill semantics separate; history is never passed through DiscordIngressAdapter.accept() again.
- [ ] Run focused history/composition tests and the full existing Discord suite.

### Task 3: Live-oriented WAITING/NOTE verification and documentation sync

Files:
- Modify: docs/CURRENT_STATE.md
- Modify: relevant Discord evidence/documentation under spec/v2/evidence/ and docs/
- Test: tests/v2/test_discord_composition.py or the existing coordination test module for active WAITING NOTE behavior

Interfaces:
- Consumes the existing binding_is_active, MessageKind.NOTE, HumanRequest exact-reply precedence, and RuntimeCoordinator state projections.
- Produces bounded evidence only for observations actually made; formal Phase 8 and D9 gates remain unchanged.

- [ ] Add or extend a failing regression test for an active WAITING_HUMAN/WAITING_RECONCILIATION binding receiving ordinary text as exactly one NOTE without a new root or cancellation.
- [ ] Run the test and confirm the failure is in the missing/insufficient assertion rather than an environment issue.
- [ ] Implement only the smallest Core-boundary adjustment required by the test; reuse existing mailbox and binding state.
- [ ] Run the focused test and existing full Discord/coordination tests.
- [ ] Start the normal RuntimeCoordinator and Discord runner for a live smoke: send a fresh request, observe progress/typing, use a waiting Task for NOTE if one is available, and verify that HumanRequest replies remain separate. Do not fabricate Human input or replay history.
- [ ] Record only bounded live observations and synchronize Current State; keep unverified typing visual inspection or active NOTE behavior explicitly pending if the external client/workload cannot produce them.

### Task 4: Full verification and delivery

Files:
- Modify: the files from Tasks 1–3 only if verification exposes a regression.

- [ ] Run focused Discord tests.
- [ ] Run python -m pytest tests/v2 -q and read the complete result.
- [ ] Run architecture checks and python -m compileall -q src recovery scripts.
- [ ] Run credential/secret scans without printing .env or raw Discord content.
- [ ] Review the diff for second schedulers, raw history persistence, full echo, token leakage, or authority changes.
- [ ] Commit the verified scoped slice and push to origin/v2/bootstrap only after the worktree contains no unrelated user changes.
- [ ] Verify exact-head CI for the pushed revision and report the remaining live external blockers.
