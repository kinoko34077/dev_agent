# Discord Core Runner Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the standalone Discord runner compose the existing durable Operation, StateStore, Human, Approval, and Process Coordination boundaries while rejecting unauthorized message ingress without any reply.

**Architecture:** Keep Discord as a thin adapter. A small runtime composition owns one existing SQLite StateStore, the pointer-only Discord binding store, the existing Operation static ingress/status functions, and one existing ProcessCoordinationService peer for intervention notes. It does not start a second scheduler or duplicate Task state; the existing RuntimeCoordinator remains the execution owner.

**Tech Stack:** Python, discord.py optional dependency, SQLiteStateStore, OperationService, ProcessCoordinationService, pytest.

**Spec:** User-provided Discord Human UI Adapter implementation instructions and the repository `AGENTS.md` rules.

## Global Constraints

- Discord must not own scheduling, retry, Task state, model routing, approval authority, or Host Verification.
- Unauthorized, malformed, bot, and duplicate ingress must not be echoed or routed.
- Discord persistence may contain only bindings, ingress idempotency, and delivery metadata; no raw message content or token.
- Existing Operation, Process Coordination, HumanInteractionPort, Approval, lease, and authority boundaries remain the source of truth.
- Portal intent configuration, OS startup registration, and production deployment remain external/deferred.
- `.env` remains ignored; secrets must never appear in source, tests, docs, evidence, logs, commits, or command output.

## Review Focus

- Unauthorized message: no echo and no Core callback.
- Duplicate message: no second Core callback or reply.
- Authorized read query: status is rendered as a bounded read projection rather than creating a Task.
- New request: exactly one durable Operation Task and pointer binding are created.
- Intervention: existing Process Coordination NOTE boundary receives only bounded metadata, never a second Discord queue or raw content.

### Task 1: Unauthorized ingress regression

**Files:**
- Modify: `tests/v2/test_discord_bot.py`
- Modify: `src/dev_agent/discord/bot.py`

**Interfaces:**
- Consumes: `build_bot`, `DiscordBotConfig`, existing `DiscordIngressAdapter`.
- Produces: `on_message` that returns before replying when ingress is rejected.

- [ ] **Step 1: Write the failing test**

  Add a fake Discord message/channel test that invokes the registered `on_message` handler for an unauthorized user and asserts no channel send occurs.

- [ ] **Step 2: Run the focused test and verify it fails**

  Run: `python -m pytest tests/v2/test_discord_bot.py -q`
  Expected: the new unauthorized-message test fails because the current handler sends an echo after `event is None`.

- [ ] **Step 3: Implement the minimal fix**

  Return immediately when `DiscordIngressAdapter.accept` returns `None`; keep bot, malformed, and duplicate inputs silent. Preserve accepted-message routing and command processing.

- [ ] **Step 4: Run the focused test and verify it passes**

  Run: `python -m pytest tests/v2/test_discord_bot.py -q`
  Expected: PASS.

### Task 2: Existing Core composition for the standard runner

**Files:**
- Create: `src/dev_agent/discord/composition.py`
- Modify: `src/dev_agent/discord/bot.py`
- Modify: `src/dev_agent/discord/__init__.py`
- Create: `tests/v2/test_discord_composition.py`

**Interfaces:**
- Consumes: `OperationConfig`, `OperationService.submit`, `OperationService.read_status`, `SQLiteStateStore`, `SQLiteDiscordBindingStore`, `SQLiteHumanInteractionPort`, `ProcessCoordinationService`, `DiscordCoreAdapter`.
- Produces: `DiscordRuntimeComposition.open(operation_config, discord_config)`, `.bindings`, `.authorizer`, `.core`, `.human`, `.close()`, and context-manager support for runner cleanup.

- [ ] **Step 1: Write failing composition tests**

  Cover new-request submission plus binding persistence, read projection without a new Task, and intervention routing to the existing NOTE mailbox with an idempotency key. Assert the coordination subject contains only bounded kind/message metadata and not the Discord message content.

- [ ] **Step 2: Run the focused composition tests and verify they fail**

  Run: `python -m pytest tests/v2/test_discord_composition.py -q`
  Expected: FAIL because the composition module does not exist.

- [ ] **Step 3: Implement the minimal composition**

  Open one `SQLiteStateStore` at `OperationConfig.state_path`, wrap it in `SQLiteDiscordBindingStore`, create `DiscordHumanAdapter` over `SQLiteHumanInteractionPort`, and use static Operation ingress/status methods. Attach one stable `discord-ui` peer to the existing `ProcessCoordinationService`; map Discord intervention kinds to `MessageKind.NOTE` with bounded metadata and message correlation. Do not create a scheduler, queue, or parallel state store. Bind the channel/thread pointer to the submitted Task root/run IDs.

- [ ] **Step 4: Inject the composition into `build_bot` and the environment runner**

  Add an optional shared `DiscordAuthorizer` parameter, use the composition’s SQLite binding store and `DiscordCoreAdapter.handle`, and keep the composition alive through `bot.run`. Close it in a `finally` block after the Gateway exits. The runner must still leave actual task execution to the existing RuntimeCoordinator.

- [ ] **Step 5: Run focused tests and verify they pass**

  Run: `python -m pytest tests/v2/test_discord_bot.py tests/v2/test_discord_core_adapter.py tests/v2/test_discord_composition.py tests/v2/test_discord_durable_flow.py -q`
  Expected: PASS.

### Task 3: Accepted read rendering and bounded runner hardening

**Files:**
- Modify: `src/dev_agent/discord/bot.py`
- Modify: `tests/v2/test_discord_bot.py`

**Interfaces:**
- Consumes: `render_read_projection`, `DiscordMessageKind.READ_QUERY`, Core callback results.
- Produces: accepted read queries render bounded status projections; accepted ordinary messages retain the existing echo behavior.

- [ ] **Step 1: Write the failing read-query rendering test**

  Invoke the handler with an authorized `今何してる` message and a Core callback returning a mapping; assert the channel receives the rendered projection and not an echo.

- [ ] **Step 2: Run the test and verify it fails**

  Run: `python -m pytest tests/v2/test_discord_bot.py -q`
  Expected: FAIL because all accepted messages currently echo.

- [ ] **Step 3: Implement the bounded projection branch**

  When the accepted event is `READ_QUERY` and the callback returns a mapping, call `render_read_projection`; otherwise retain `render_echo` for accepted messages. Keep exceptions fail-closed and never expose callback internals.

- [ ] **Step 4: Run the focused Discord suite**

  Run: `python -m pytest tests/v2/test_discord_bot.py tests/v2/test_discord_core_adapter.py tests/v2/test_discord_composition.py tests/v2/test_discord_durable_flow.py -q`
  Expected: PASS.

### Task 4: Repository verification and documentation sync

**Files:**
- Modify: `docs/CURRENT_STATE.md`
- Modify: `docs/SYSTEM_MAP.md` or the owning Discord documentation identified during review
- Modify: relevant Discord Evidence index only if the implementation evidence is actually captured

**Interfaces:**
- Consumes: focused tests and read-only Gate state.
- Produces: accurate documentation that standard runner composition is local-verified while real Discord Gateway remains blocked by Portal Message Content Intent.

- [ ] **Step 1: Run focused tests, architecture, and compile checks**

  Run the focused Discord suite, the architecture checker used by the repository, and `python -m compileall src scripts`.

- [ ] **Step 2: Run the full v2 regression**

  Run: `python -m pytest tests/v2 -q`
  Expected: all tests pass with only the known Windows ACL deployment-owned skip, if unchanged.

- [ ] **Step 3: Update documentation without secrets or Gate promotion**

  Record the standard runner composition and unauthorized-ingress behavior. Keep `PHASE8 LIVE_ACTIVATION = NOT_VERIFIED`, `D9_PRODUCTION_DEPLOYMENT = DEFERRED_NOT_READY`, and the Portal intent blocker unchanged.

- [ ] **Step 4: Commit, push, and verify exact-head CI**

  Stage only source/tests/docs/plan files, commit with a scoped message, push `v2/bootstrap` to `origin`, and verify the pushed SHA plus exact-head CI. Never stage `.env`.
