# Process Coordination Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize the current v2 baseline, remove confirmed documentation drift, and add a small independent Process Coordination Plane for peer presence, durable mailbox delivery, immutable handoff artifacts, and generation-safe coordination without changing Task scheduling, Provider routing, OperationControl, Recovery authority, or D9 production mutation.

**Architecture:** Coordination is a separate source package under `src/dev_agent/coordination/` with its own SQLite database and immutable artifact directory. Typed protocol objects own validation and JSON shape; the store owns durable transactions; the service composes them into peer/mailbox/handoff operations. No coordination component starts, stops, kills, schedules, retries, integrates, or approves a Task. Stage D Guardian and Stage E D9 mutation remain deferred until Stage C is verified.

**Tech Stack:** Python 3.10+, stdlib `dataclasses`, `enum`, `json`, `sqlite3`, `pathlib`, SHA-256, existing `src.dev_agent._sqlite.connect`, existing audit redaction, pytest, repository architecture and compile checks.

**Spec:** `dev_agent 安定化・軽量化・Process Coordination・D9移行 実装指示書` (2026-09-14 user instruction).

## Global Constraints

- Preserve `v2/bootstrap`, existing Task/Scheduler/Operation/Provider/Recovery/Host Verification/Authority boundaries, and the D9 proposal-only boundary.
- Do not add a second Task scheduler, retry engine, Agent framework, process killer, or process-start authority.
- Do not add live Gemini, Compression, MCP wire, paid-provider, or external Codex calls for this foundation.
- Coordination state is runtime data outside the repository's tracked source; tests must use temporary directories.
- Use at-least-once mailbox delivery with idempotent enqueue/claim/ack semantics; do not promise exactly-once delivery.
- Treat peer generation and mailbox claim ownership as fencing data; stale instances must not mutate newer state.
- Keep all stored/logged payloads bounded and redact secret-shaped values. Never store credentials or raw AI conversations.
- Use `apply_patch` for edits and make focused commits. Never use destructive cleanup or reset commands.

---

## Stage A — Baseline and documentation drift

- [ ] Re-run current branch/HEAD/dirty state, Gate check, focused read-only checks, and the full `tests/v2` baseline. Record actual results before code changes.
- [ ] Update `README.md` only where it gives materially false current v2 guidance: CodexExecBackend/MCP transport-neutral adapter/Self-Improvement status and stale test counts. Keep historical v1 content explicitly historical and do not copy Current State into README.
- [ ] Run documentation link/reference checks available in the repository plus architecture and compile checks; create a focused docs commit.
- [ ] Confirm G6O1 remains `NOT VERIFIED`/deferred and that formal Codex post-restart discovery remains `NOT_AVAILABLE`; do not change gate evidence.

## Stage B — Narrow boundary guardrails

- [ ] Add a small `coordination` package initializer and document its ownership in `docs/SYSTEM_MAP.md` and `docs/requirements/process-coordination/` without changing runtime behavior.
- [ ] Extend the architecture check with a deterministic rule preventing one `scripts/devfarm*.py` module from importing underscore-prefixed symbols from another `scripts/devfarm*.py` module. Preserve existing allowlists only where a current public compatibility boundary requires them, and add a regression test for the rule.
- [ ] Run the architecture check and targeted tests after the guardrail; do not refactor the large Core modules merely because of file size.

## Stage C1 — Typed coordination protocol

- [ ] Implement `src/dev_agent/coordination/protocol.py` with bounded JSON-safe protocol types: `PeerStatus`, `MessageKind`, mailbox status, `PeerRecord`, `MailboxMessage`, `ArtifactReference`, and `HandoffNote`.
- [ ] Validate safe identifiers, positive generations, optional positive PIDs, bounded revision/timestamps, capabilities, artifact references, expiry values, and JSON-safe fields. Reject secret-shaped keys/values and unknown enum values fail-closed.
- [ ] Keep message kinds for notification versus authority requests distinct (`NOTE`, `HANDOFF`, `ARTIFACT_READY`, `WORK_REQUEST`, `REVIEW_REQUEST`, `CONTROL_REQUEST`, `CHECKPOINT`); do not grant authority by message kind.
- [ ] Add protocol round-trip and invalid-input tests in `tests/v2/test_process_coordination_protocol.py`.

## Stage C2 — Separate durable Coordination Store

- [ ] Implement `src/dev_agent/coordination/store.py` using the existing SQLite connection defaults and a separate schema version. Create peer, mailbox, and idempotency tables with indexes and bounded transaction scopes.
- [ ] Implement peer registration, exact-generation heartbeat, detach, lookup/list, and lease-expiry observation. Expired leases become a detectable degraded/lost-presence condition and never silently become a healthy peer.
- [ ] Implement mailbox enqueue with idempotency-key deduplication, claim with bounded lease/attempt data, ACK requiring the claim owner, and reclaim of expired claims. Preserve at-least-once delivery and return durable message records.
- [ ] Make duplicate idempotency requests return the existing message only when the immutable request shape matches; reject conflicting reuse.
- [ ] Add restart/reopen, contention-safe transaction, generation-fencing, expiry, duplicate enqueue, reclaim, wrong-owner ACK, and separate-database tests in `tests/v2/test_process_coordination_store.py`.

## Stage C3 — Immutable artifacts and service composition

- [ ] Implement `src/dev_agent/coordination/artifacts.py` for bounded, content-addressed immutable JSON/text artifacts confined under the configured coordination data directory. Use the existing safe immutable-write pattern or an equivalent exclusive/atomic implementation; never overwrite an existing digest path.
- [ ] Implement `src/dev_agent/coordination/service.py` as the narrow composition boundary: attach/detach/heartbeat/expire peers, send/claim/ack mailbox messages, and write/read immutable handoff notes. It must not own Task selection, process operations, approval, budget, retry, integration, or scheduling.
- [ ] Resolve the runtime directory lazily from an explicit constructor path or `DEV_AGENT_DATA_DIR`; never read secrets or perform network I/O at import time. Default coordination DB path is `<data_dir>/coordination/coordination.sqlite3` and artifacts are under `<data_dir>/coordination/artifacts/`.
- [ ] Add service tests proving a handoff survives process/service reopen, artifact references carry path/sha256/size/kind/revision, path escape and overwrite are rejected, and duplicate message delivery remains idempotent.

## Stage C4 — Documentation and integration boundary

- [ ] Add a concise coordination requirement/ADR describing separate lifecycle ownership, SQLite plus immutable artifacts, at-least-once delivery, generation fencing, and the reason Agent/Codex never directly restart one another.
- [ ] Update `docs/SYSTEM_MAP.md`, `docs/CURRENT_STATE.md`, `docs/V2_DETAILED_ROADMAP.md`, and `spec/v2/TRACEABILITY.md` only with verified Stage C capability and explicit Stage D/E gaps. Do not claim resident Agent, Guardian, rolling restart, or D9 real mutation.
- [ ] Add a focused acceptance/evidence artifact for protocol/store/service behavior without secrets or raw conversations.
- [ ] Run focused coordination tests, full `python -m pytest tests/v2 -q`, `python scripts/check_architecture.py`, and `python -m compileall -q src recovery scripts`.
- [ ] Commit and push the verified Stage C slice to `origin/v2/bootstrap`, then confirm exact-head GitHub Actions before starting Guardian work.

## Deferred follow-up (do not implement in this plan)

- Guardian process start/stop/restart authority, OS service integration, drain/checkpoint, rolling restart, revision-pinned runtime, and crash drills.
- Agent/Codex runtime attach integration beyond the durable protocol/service foundation.
- D9 real mutating self-repair, official runtime promotion, automatic push/merge, paid-provider activation, Compression, MCP wire transport, or Codex-less official integration.

## Verification checklist

- [ ] Current baseline and all failures classified with actual evidence.
- [ ] `README.md` no longer directs operators using known false v2 status.
- [ ] Cross-module private DevFarm imports are guarded.
- [ ] Protocol round-trip/validation tests pass.
- [ ] Coordination DB is separate from Task State and survives reopen.
- [ ] Peer heartbeat/lease/generation semantics are durable and fenced.
- [ ] Mailbox is at-least-once and idempotency-safe.
- [ ] Handoff artifacts are immutable, bounded, content-addressed, and non-secret.
- [ ] No Task scheduler, process-control, authority, or D9 boundary was added or weakened.
- [ ] Full regression, architecture, compile, and exact-head CI pass.
