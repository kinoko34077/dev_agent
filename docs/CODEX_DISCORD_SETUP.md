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

The current MVP echoes human messages as `受信: ...`, ignores Bot-authored
messages, and exposes `/dir` and `/file` only as bounded repository-relative
scope/reference commands. A live Gateway result requires the external Portal,
server installation, intent, permission, and token setup above; local tests do
not claim that external evidence.
