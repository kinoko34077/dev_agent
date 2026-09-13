# Phase 6B/C to Phase 7A-C Transition Plan

> **Scope:** Continue the roadmap after the Phase 6A quota/provider foundation
> without claiming live cloud or paid qualification evidence.

**Goal:** Make quota-aware free-first operation conservative across shared quota
domains, then introduce a deterministic Task profile/intelligence boundary and
an evaluator seam that is independent from the executing provider.

**Architecture:** ResourceLedger remains the durable source for resource and
quota observations. Provider responses may publish only the normalized
`usage.quota_observation` mapping; the control plane ingests it after a valid
response and records whether ingestion succeeded. A shared quota domain is
never summed across credentials: the router uses the most conservative fresh
headroom. Task classification and intelligence tier are typed policy data,
not model-selected authority. Evaluator decisions are durable records only
after an explicit result is supplied; execution and evaluation stay separate.

**Non-goals:** No paid request, no live provider claim, no hedged dispatch, no
automatic self-improvement, no AgentBackend/MCP/Codex connection, and no
Phase 7 promotion of the Gate while G6O1 remains externally blocked.

## Track A — Phase 6B/C quota and free-first hardening

- Add latest-per-resource quota observation lookup by `quota_domain`.
- Compute conservative domain headroom without adding observations from
  multiple credentials.
- Ingest the normalized `usage.quota_observation` response mapping after a
  provider response, without turning malformed auxiliary telemetry into a
  successful charge/result or a duplicate retry.
- Reject resources at or over their concurrency limit before routing.
- Add tests for shared-domain non-summing, response ingestion/reload, invalid
  telemetry isolation, and quota/outage fallback.

## Track B — Phase 7A/B typed task profile and intelligence policy

- Add typed `TaskType`, `RiskLevel`, and `IntelligenceTier` values while
  preserving existing `TaskClass` (`normal`/`recovery`) authority semantics.
- Persist profile fields in the existing Task JSON payload without changing
  the SQLite schema.
- Map task type, required capabilities, and risk to a minimum tier and bounded
  allowed tier deterministically. A model cannot raise its own tier or policy
  authority.
- Include the profile in the normalized ModelRequest metadata and audit-visible
  request data without changing the canonical Dispatcher path.

## Track C — Phase 7C independent evaluator seam

- Define typed evaluator outcomes: PASS, RETRY_SAME, RETRY_OTHER_PROVIDER,
  ESCALATE, WAIT_HUMAN, and FAIL.
- Evaluate deterministic evidence first (test/lint/build/diff/policy), with
  explicit unknown evidence rather than optimistic success.
- Persist evaluation records durably with task/run/evidence references; do not
  let the executing provider self-certify a result.
- Keep escalation execution bounded and defer actual AgentBackend integration.

## Verification and evidence

- Every behavior change starts with a failing focused test.
- Run focused tests, full pytest, compileall, JSON/document checks, and the
  existing Gate checker after each track.
- Commit and push each coherent track. Exact-head CI remains external evidence;
  G6O1 stays `BLOCKED_EXTERNAL`.

## Remaining after this plan

Live qualification for free/remote providers, provider-specific quota header
adapters, multi-credential operational aggregation beyond conservative domain
headroom, delayed hedge, workflow promotion, self-improvement, AgentBackend,
MCP, Codex integration, and Phase 7D onward remain subsequent work.
