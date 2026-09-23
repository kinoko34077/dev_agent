# Discord Human UI Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a thin, optional `discord.py` Human UI adapter that can receive Discord messages and commands, render bounded progress/Human requests, and delegate to existing dev_agent Human/Operation boundaries without creating a second runtime.

**Architecture:** The Discord package owns only Gateway ingress, Discord identity/path checks, idempotency, channel/root binding, and bounded rendering. Existing `HumanInteractionPort`, Approval authority, Process Coordination, Operation, and RuntimeCoordinator remain the source of truth. The first implementation keeps `discord.py` optional so core tests do not require a live Discord account or Gateway.

**Tech Stack:** Python 3.10+, optional `discord.py` 2.x, existing SQLite StateStore and HumanInteractionPort, pytest.

**Spec:** `dev_agent Discord Human UI Adapter 実装指示書` supplied in the user request; existing `docs/requirements/multi-free-provider/05-agent-backend-codex-mcp-and-authority.md` and `src/dev_agent/human/` contracts remain authoritative.

**Status (2026-09-23):** Tasks 1–4 are implemented and locally verified. Task 5
is being synchronized with local Evidence. Gateway/token/Portal setup and live
Discord E2E remain external prerequisites; formal D9/Phase 8 Gates are unchanged.

## Global Constraints

- Never commit `DISCORD_BOT_TOKEN`, `.env`, credentials, raw Discord messages, or raw provider output.
- `DISCORD_APPLICATION_ID` and `DISCORD_PUBLIC_KEY` may exist only in the ignored local `.env`; `.env.example` contains placeholders only.
- The Discord adapter has no Scheduler, Worker, Planner, retry engine, Approval store, Task state machine, or arbitrary shell access.
- Discord buttons and messages delegate to existing Human/Approval authority; the adapter never self-approves or promotes a Gate.
- Human responses correlate to an exact `request_id` and are consumed once through the existing HumanInteractionPort.
- Bot Gateway availability and Developer Portal configuration are external evidence; local tests must not claim live Discord connectivity.
- Discord offline/restart behavior uses existing durable state and bindings; Discord history is not a Task-state source of truth.

## Review Focus

- Missing token or optional dependency must fail with a bounded operator error and must not log secrets.
- Bot-authored messages and duplicate Discord message/interaction IDs must not create duplicate work.
- `/dir` and `/file` must remain preferred scope/reference only and reject workspace escapes or protected paths.
- Human buttons/replies must reject unauthorized identities, wrong request IDs, duplicate consumption, and Codex Expert responses.
- Progress and Human-request rendering must be bounded and must not expose credentials or raw conversation history.

---

### Task 1: Optional Discord dependency and local operator configuration

**Files:**
- Create: `requirements-discord.txt`
- Create: `.env.example`
- Create: `.env` (ignored local operator file; never stage)
- Modify: `.gitignore` only if the existing `.env` rules are insufficient
- Test: `tests/v2/test_discord_config.py`

**Interfaces:**
- Produces `DISCORD_APPLICATION_ID`, `DISCORD_PUBLIC_KEY`, and `DISCORD_BOT_TOKEN` environment names for later tasks.
- `DISCORD_BOT_TOKEN` remains unset in repository configuration and tests.

- [ ] **Step 1: Write the failing configuration tests**

```python
def test_env_example_contains_placeholders_without_real_identifiers():
    content = Path(".env.example").read_text(encoding="utf-8")
    assert "DISCORD_APPLICATION_ID=" in content
    assert "DISCORD_PUBLIC_KEY=" in content
    assert "DISCORD_BOT_TOKEN=" in content
    assert "1552123238543523910" not in content
    assert "be400902" not in content


def test_discord_dependency_is_optional_from_core_requirements():
    assert "discord.py" not in Path("requirements-v2-dev.txt").read_text(encoding="utf-8")
    assert "discord.py" in Path("requirements-discord.txt").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the tests and verify the configuration assertions fail**

Run: `python -m pytest tests/v2/test_discord_config.py -q`
Expected: FAIL because the optional dependency/configuration files do not yet exist.

- [ ] **Step 3: Add optional dependency and ignored local configuration**

`requirements-discord.txt` contains only `discord.py>=2.3,<3`.
`.env.example` contains blank placeholders and comments explaining that the Bot Token is operator-supplied.
`.env` contains the user-provided application ID and public key plus an empty token assignment; it stays ignored.

- [ ] **Step 4: Run the tests and inspect Git tracking**

Run: `python -m pytest tests/v2/test_discord_config.py -q` and `git status --short --ignored .env .env.example`.
Expected: tests PASS; `.env` is ignored and `.env.example` is visible as a normal repository file.

- [ ] **Step 5: Commit**

```bash
git add requirements-discord.txt .env.example tests/v2/test_discord_config.py
git commit -m "build: add optional discord operator configuration"
```

Do not add `.env`.

### Task 2: Pure Discord ingress and scope boundary

**Files:**
- Create: `src/dev_agent/discord/__init__.py`
- Create: `src/dev_agent/discord/adapter.py`
- Create: `src/dev_agent/discord/auth.py`
- Create: `src/dev_agent/discord/binding.py`
- Test: `tests/v2/test_discord_adapter.py`

**Interfaces:**
- `DiscordMessage(message_id, author_id, channel_id, guild_id, thread_id, content)` is bounded ingress data.
- `classify_message(content)` returns `NOTE`, `PARALLEL`, `INTERRUPT`, `CANCEL`, `READ_QUERY`, or `NEW_REQUEST` without treating every message as CANCEL.
- `DiscordAuthorizer.is_allowed(author_id, guild_id, channel_id)` checks numeric IDs only.
- `DiscordScope.resolve_directory` and `resolve_file` return repository-relative paths after existing protected/path policy checks.
- `DiscordBindingStore` maps Discord `(guild_id, channel_id, thread_id)` to a `root_id`/`run_id` pointer; it does not copy Task state.

- [ ] **Step 1: Write failing tests** for bot-message filtering, duplicate message IDs, intervention classification, numeric identity authorization, workspace escape rejection, protected-path rejection, and pointer-only binding.
- [ ] **Step 2: Run `python -m pytest tests/v2/test_discord_adapter.py -q` and verify the expected missing-import failures.**
- [ ] **Step 3: Implement bounded pure-Python adapter/auth/binding classes using existing path-policy helpers and in-memory test doubles only.**
- [ ] **Step 4: Re-run the focused tests and then the existing Process Coordination/path-policy tests.**
- [ ] **Step 5: Commit `feat: add bounded discord ingress boundary`.**

### Task 3: Phase A echo bot and Phase B UI rendering

**Files:**
- Create: `src/dev_agent/discord/bot.py`
- Create: `src/dev_agent/discord/renderer.py`
- Create: `scripts/run_discord_bot.py`
- Create: `tests/v2/test_discord_bot.py`
- Create: `docs/CODEX_DISCORD_SETUP.md`

**Interfaces:**
- `build_bot(config, adapter)` constructs a `discord.py` bot only when the optional dependency is installed.
- `run_from_environment()` loads process environment plus the ignored local `.env` through a bounded loader and fails closed when `DISCORD_BOT_TOKEN` is missing.
- `render_progress(event)` and `render_human_request(request)` produce bounded, redacted text/embeds.
- Phase A `on_message` ignores `message.author.bot`, echoes bounded content, and calls `bot.process_commands`.
- `/dir` and `/file` call the pure adapter scope boundary; they do not access arbitrary PC paths.

- [ ] **Step 1: Write tests** for missing-token failure, bounded echo, bot self-message ignore, renderer redaction/bounds, and command registration shape. Tests use a fake Discord module or skip live Gateway calls when `discord.py` is absent.
- [ ] **Step 2: Verify RED.**
- [ ] **Step 3: Implement lazy optional imports, bot factory, bounded configuration, echo handler, `/dir`, `/file`, and operator setup documentation.**
- [ ] **Step 4: Run focused tests, `python scripts/run_discord_bot.py --help`, Architecture, and compileall.**
- [ ] **Step 5: Commit `feat: add discord thin human ui mvp`.**

### Task 4: HumanRequest/Approval and durable adapter seams

**Files:**
- Modify: `src/dev_agent/discord/adapter.py`
- Modify: `src/dev_agent/discord/bot.py`
- Modify: `src/dev_agent/human/port.py` only for an existing-port-compatible seam
- Test: `tests/v2/test_discord_human_flow.py`
- Evidence: `spec/v2/evidence/discord-human-ui-local-20260923.json`

**Interfaces:**
- Human questions call the existing `HumanInteractionPort.request_human`; Discord response/button callbacks pass an exact `HumanResponse` through `record_response`/`consume_response`.
- Approval buttons call an injected existing approval callback with the Discord numeric identity and exact approval ID; the adapter does not decide approval semantics.
- Duplicate interaction IDs and duplicate response consumption are rejected durably.

- [ ] **Step 1: Write failing tests** for HumanRequest notification, authorized response, unauthorized response, wrong request ID, duplicate consume, Expert-vs-Human authority separation, and unrelated READY continuation using existing RuntimeCoordinator tests.
- [ ] **Step 2: Verify RED.**
- [ ] **Step 3: Implement the narrow adapter callback path and persistent pointer/delivery metadata only where the existing state contract permits it.**
- [ ] **Step 4: Run focused/full regression and create bounded Evidence with no token/raw message.**
- [ ] **Step 5: Commit `feat: connect discord human request boundary`.**

### Task 5: Roadmap synchronization and live boundary check

**Files:**
- Modify: `docs/CURRENT_STATE.md`
- Modify: `docs/SYSTEM_MAP.md`
- Modify: `docs/V2_EXECUTION_PLAN.md` or `docs/V2_DETAILED_ROADMAP.md` only where the existing Discord/MCP lane belongs
- Modify: `spec/v2/TRACEABILITY.md`

- [ ] **Step 1: Record only local evidence and external setup prerequisites.**
- [ ] **Step 2: Run full `tests/v2`, Architecture, compileall, JSON/diff/secret checks.**
- [ ] **Step 3: Push and confirm exact-head CI.**
- [ ] **Step 4: Do not claim live Discord Gateway, Button E2E, restart/offline delivery, or Phase 8 Gate promotion until a Bot Token, Message Content Intent, server install, and real event evidence exist.**
