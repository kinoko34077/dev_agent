# Discord Human UI setup

The Discord adapter is an optional thin UI. It does not own Tasks, scheduling,
retry, approvals, model routing, or repository mutation. Those remain in the
existing dev_agent Core.

## Developer Portal

1. Open the Discord Developer Portal.
2. Create or open the application supplied by the operator.
3. Create a Bot user and copy its Bot Token once.
4. Enable **Message Content Intent** for natural-message ingress.
5. Install the Bot into the target server with only the required permissions:
   View Channels, Send Messages, Read Message History, Embed Links, and Attach
   Files only when needed.
6. Do not grant Administrator.

The Bot Token is never stored in Git, tests, Evidence, or logs. The local
ignored `.env` contains the application ID and public key supplied by the
operator; add the token locally before starting the Bot.

## Local environment

Copy `.env.example` to `.env` only if the ignored file does not already exist,
then set:

```text
DISCORD_APPLICATION_ID=<application id>
DISCORD_PUBLIC_KEY=<64 hex public key>
DISCORD_BOT_TOKEN=<operator-supplied bot token>
DISCORD_ALLOWED_USER_ID=<numeric Discord user id>
DISCORD_ALLOWED_GUILD_ID=<numeric Discord guild id>
DISCORD_ALLOWED_CHANNEL_ID=<numeric Discord channel id>
```

The allowlist is fail-closed for operational commands. Display names and
nicknames are not authority identities.

Install the optional dependency from the repository root:

```powershell
python -m pip install -r requirements-discord.txt
```

Start the Phase A/early Phase B adapter:

```powershell
python scripts/run_discord_bot.py
```

The adapter acknowledges accepted human messages with short bounded text rather
than repeating the full message. Human-facing sends use one UI boundary with a
default two-second typing indicator and per-channel/thread serialization; this
is not a Task queue. The adapter ignores Bot-authored messages and exposes
`/dir` and `/file` as bounded repository-relative scope commands. Scope is
persisted in the existing Core SQLite StateStore and is passed to the next
Operation or child Operation as structured input. Plain text sent while a
bound run is non-terminal becomes a NOTE; explicit `/parallel`, `/interrupt`,
and `/cancel` retain their Core meanings. The standard runner also starts the
read-only outbound projection after Gateway readiness; it observes existing
Core state for progress and pending HumanRequest delivery without owning Tasks,
the queue, retry policy, or Approval authority.

For a new request or bounded parallel request, the adapter may attach up to 20
sanitized messages (8,000 characters total) from the same channel/thread as
`inputs["discord_context"]`. Only the authorized Human and this Bot are
included, the current message is excluded, and the history is context only: it
is never replayed into ingress, the mailbox, or an Approval/HumanResponse.

Replies to a delivered HumanRequest are correlated by the Discord reply
reference before ordinary ingress. Finite answers can use buttons and free-text
requests use the same exact HumanInteractionPort request identity. Approval
buttons, when projected, delegate to the existing Core-owned Approval
reference; Discord does not create approval authority.

## Live message smoke order

The runner does not backfill Discord history. Use this order:

1. Start `python scripts/run_discord_bot.py` and keep it running.
2. Wait for `Discord Human UI bot ready`.
3. From the authorized Human Discord client, send a **new** message.
4. Confirm the echo, Core ingress/binding result, and any bounded outbound
   projection in the same channel or thread. Start the existing
   RuntimeCoordinator separately when you want the submitted Operation to be
   claimed and executed.

Posting a message before starting or restarting the runner does not test the
Gateway ingress path. A real interactive client is required for live message,
progress, HumanRequest, and button evidence; local tests and Gateway login do
not claim that external E2E. The same applies to real progress, HumanRequest
reply, restart/offline delivery, and Approval button round-trips. Approval
button delivery additionally requires an explicit Core-owned approval
view/submit boundary; the Discord adapter never creates approval authority
itself.
